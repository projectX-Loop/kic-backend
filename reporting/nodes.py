"""노드 11개 (흐름도 최종본). 지금은 틀만: 가짜 LLM·가짜 툴로 돌고 로그만 남긴다.

역할 나눔 (기획서 원칙):
  LLM  = 해석·계획·툴 선택·진행 판단·글쓰기   (understand, planner, executor, progress_judge, replanner, composer)
  코드 = 계산·검사·분기·값 채우기             (plan_checker, report_checker, 이름표 채우기, 한도)
  사람 = 확정                                 (human_review)
정정(영향 절만 재작성) 기능은 이번 범위에서 뺐다.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from .config import (LOG_DIR, NODE_NAMES, MAX_REPLANS, MAX_TASKS, MAX_TOOL_CALLS_PER_TASK,
                    PLAN_RETRY, REPORT_REWRITE)
from .fakes import TEMPLATES, TOOLS
from .llm import LLMFailed, call_json
from .runlog import get_logger, logged_node

TAG = re.compile(r"\{([A-Za-z0-9_]+)\}") # 이름표 {참조ID} (지표·검증 결과 등의 ID)
RAW_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|x|배|bp)") # LLM이 직접 쓴 숫자
STATUS_WORDS = re.compile(r"(?<!불)일치|검증 완료|비교 가능") # 상태 판정은 코드·사람 몫 (LLM이 직접 쓰면 안 됨)
BUILTIN_TAGS = {"n_review"} # 코드가 계산해서 채우는 이름표


def _log(state: dict):
    return get_logger(state["run_id"])


def _bump(state: dict, **kw: int) -> dict[str, int]:
    c = dict(state.get("counters", {}))
    for k, v in kw.items():
        c[k] = c.get(k, 0) + v
    return c


# 실패 경로 (실패를 잡아 사유를 남기고 마무리 단계로 보낸다)
def _fail_before(state: dict, node: str, e: LLMFailed) -> dict:
    """실행 전 실패: 쓸 근거가 없으니 보고서 없이 실패 사유만 기록."""
    _log(state).log(node, "error", "fail", f"{e} → 보고서 없이 실패 기록")
    return {"notice": f"{NODE_NAMES[node]} 실패: {e}", "aborted": True,
            "counters": _bump(state, llm_calls=e.attempts)}


def _fail_during(state: dict, node: str, reason: str, plan: list[dict] | None = None, llm_calls: int = 0) -> dict:
    """실행 중 실패: 모은 근거로 작성하고 끝내지 못한 작업은 미완으로 표시."""
    _log(state).log(node, "error", "fail", f"{reason} → 모은 근거로 작성 (미완 표시)")
    plan = [{**t, "status": "failed"} if t["status"] in ("pending", "in_progress") else t
            for t in (plan or state["plan"])]
    return {"plan": plan, "stop_reason": f"{NODE_NAMES[node]} 실패: {reason}", "aborted": True,
            "progress": {"decision": "finish", "reason": reason}, "counters": _bump(state, llm_calls=llm_calls)}


# =================================================================== 1. 대화 시작
@logged_node("start")
def start(state: dict, config: RunnableConfig) -> dict:
    scenario = config["configurable"].get("scenario", "normal")
    _log(state).log("start", "run_start", summary=f"시나리오={scenario} | {state['request'].get('request_text', '')}",
                    data={"request": state["request"], "scenario": scenario})
    return {"status": "running", "notice": None, "stop_reason": None, "aborted": False, "plan_source": None,
            "plan": [], "plan_check": None, "plan_retry": 0,
            "replan_count": 0, "current_task": None, "evidence": {}, "progress": None, "draft": None,
            "rewrite_count": 0, "check": None, "review_queue": [], "review": None, "review_error": None,
            "counters": {"llm_calls": 0, "tool_calls": 0}}


# =================================================================== 2. 양식·요청 이해 (코드 필수값 검사 + LLM)
PERIOD = re.compile(r"^\d{4}Q[1-4]$") # 기준일 형식 (예: 2025Q2)


def _missing_request_fields(req: dict) -> list[str]:
    """LLM에 맡기기 전에 코드가 요청 필수값을 확인한다 (기획서: 양식 정보 부족 → 작성 보류)."""
    missing = []
    if not req.get("template_id"):
        missing.append("양식 종류")
    if not isinstance(req.get("funds"), list) or not req["funds"]:
        missing.append("대상 펀드")
    if not isinstance(req.get("as_of"), str) or not PERIOD.match(req["as_of"]):
        missing.append("기준일(예: 2025Q2)")
    return missing


@logged_node("understand")
def understand(state: dict, config: RunnableConfig) -> dict:
    if missing := _missing_request_fields(state["request"]):
        _log(state).log("understand", "check", "fail", f"요청 필수값 없음 {missing} → 작성 보류 (LLM 호출 안 함)",
                        {"missing": missing})
        return {"notice": f"정보 부족 — 필요한 정보: {missing}"}
    try:
        spec, n = call_json("understand", "understand", state, config,
                            required=("template_id", "out_of_scope", "missing_info"))
    except LLMFailed as e:
        return _fail_before(state, "understand", e)
    update: dict[str, Any] = {"spec": spec, "counters": _bump(state, llm_calls=n)}
    if spec["out_of_scope"]:
        update["notice"] = f"범위 밖 요청: {spec.get('reason', '')}"
    elif spec["missing_info"]:
        update["notice"] = f"정보 부족 — 필요한 정보: {spec['missing_info']}"
    elif spec["template_id"] not in TEMPLATES:          # 양식 찾기는 코드
        update["notice"] = f"없는 양식: {spec['template_id']}"
    else:
        update["template"] = TEMPLATES[spec["template_id"]]
    return update


# =================================================================== 3. 작업 계획 (LLM)
@logged_node("planner")
def planner(state: dict, config: RunnableConfig) -> dict:
    try:
        out, n = call_json("planner", "plan", state, config, required=("tasks",))
    except LLMFailed as e:
        return _fail_before(state, "planner", e)
    tasks = [{**t, "status": "pending"} for t in out["tasks"]]
    _log(state).log("planner", "check", summary=f"작업 {len(tasks)}개: " + " / ".join(t["goal"] for t in tasks),
                    data={"tasks": tasks})
    return {"plan": tasks, "current_task": None, "plan_source": "planner", "counters": _bump(state, llm_calls=n)}


# =================================================================== 4. 계획 검사 (코드)
# 작업 상태 : pending → in_progress → completed
#   재계획 시 진행 중·남은 작업은 replaced(이력으로 남김), 새 작업이 뒤에 붙는다. 실패는 failed.
def _plan_violations(plan: list[dict], template: dict, request: dict) -> list[str]:
    """아직 안 한 작업(pending)만 검사한다. 끝난 작업은 다시 보지 않는다."""
    v: list[str] = []
    funds, as_of = request["funds"], request["as_of"]
    new = [t for t in plan if t["status"] == "pending"]
    done = {t["id"] for t in plan if t["status"] == "completed"}
    section_ids = {s["id"] for s in template["sections"]}
    if len(new) > MAX_TASKS:
        v.append(f"남은 작업 수 {len(new)}개 > 한도 {MAX_TASKS}개")
    for t in new:
        if bad := [s for s in t["sections"] if s not in section_ids]:
            v.append(f"작업{t['id']}: 양식에 없는 절 {bad}")
        if not t["targets"]:
            v.append(f"작업{t['id']}: 대상 펀드 없음")
        if bad := [f for f in t["targets"] if f not in funds]:
            v.append(f"작업{t['id']}: 요청에 없는 펀드 {bad}")
        if t.get("period") != as_of:
            v.append(f"작업{t['id']}: 요청과 다른 기간 {t.get('period')} (요청 {as_of})")
        # 끝난 작업이나 '앞' 작업에만 의존 → 순환 불가
        if bad := [d for d in t["depends_on"] if d not in done and not (d < t["id"] and d in {x["id"] for x in new})]:
            v.append(f"작업{t['id']}: 끝난 작업·앞 작업이 아닌 것에 의존 {bad}")
    covered = {s for t in plan if t["status"] in ("completed", "pending") for s in t["sections"]}
    if missing := [s["id"] for s in template["sections"] if s["required"] and s["id"] not in covered]:
        v.append(f"필수 절 빠짐 {missing}")
    return v


@logged_node("plan_checker")
def plan_checker(state: dict, config: RunnableConfig) -> dict:
    log = _log(state)
    after_replan = state.get("plan_source") == "replanner"
    v = _plan_violations(state["plan"], state["template"], state["request"])
    log.log("plan_checker", "check", "fail" if v else "ok",
            ("재계획 " if after_replan else "") + ("; ".join(v) if v else "위반 없음"), {"violations": v, "after_replan": after_replan})
    if not v:
        return {"plan_check": {"ok": True, "violations": [], "next": "executor"}}
    if after_replan:
        # 재계획이 걸리면 처음부터 다시 계획하지 않는다. 모은 근거로 작성하고 남은 작업은 미완으로 표시.
        log.log("plan_checker", "limit_hit", "fail", "재계획이 검사에 걸림 → 지금까지 근거로 작성 (미완 표시)")
        plan = [{**t, "status": "failed"} if t["status"] == "pending" else t for t in state["plan"]]
        return {"plan_check": {"ok": False, "violations": v, "next": "composer"}, "plan": plan,
                "stop_reason": "재계획 검사 실패: " + "; ".join(v)}
    if state["plan_retry"] < PLAN_RETRY:
        return {"plan_check": {"ok": False, "violations": v, "next": "planner"},
                "plan_retry": state["plan_retry"] + 1}
    log.log("plan_checker", "limit_hit", "fail", f"계획 재시도 {PLAN_RETRY}회 소진 → 작성 보류 (아직 실행 전이라 쓸 근거 없음)")
    return {"plan_check": {"ok": False, "violations": v, "next": "commit"},
            "notice": "계획 검사 실패: " + "; ".join(v)}


# =================================================================== 5. 실행기 (LLM 툴 선택 + 툴 호출 루프)
@logged_node("executor")
def executor(state: dict, config: RunnableConfig) -> dict:
    log = _log(state)
    done = {t["id"] for t in state["plan"] if t["status"] == "completed"}
    pending = [i for i, t in enumerate(state["plan"]) if t["status"] == "pending"]
    # 의존하는 작업이 모두 끝난 첫 pending 작업. 없으면 막힌 것 → 실패 경로
    idx = next((i for i in pending if set(state["plan"][i]["depends_on"]) <= done), None)
    if idx is None:
        return _fail_during(state, "executor", "실행할 수 있는 작업 없음 (의존 관계 막힘)")
    task = state["plan"][idx]
    log.log("executor", "check", summary=f"작업{task['id']} 시작: {task['goal']}", data={"task": task})

    # 툴 루프: LLM이 툴 하나를 고름 → 코드가 실행 → 결과를 보고 다음 툴 … → 툴을 안 고르면(done) 끝
    plan = [dict(t) for t in state["plan"]]
    plan[idx]["status"] = "in_progress"
    as_of = state["request"]["as_of"]
    evidence = dict(state["evidence"])
    tool_log: list[dict] = []        # 이 작업에서 부른 툴과 결과·에러 (다음 판단 때 LLM에 보여 줌, State엔 안 남김)
    used, llm, limited = 0, 0, []
    # 툴 최대 N번 + 마지막에 "끝"을 듣는 1번 = LLM 최대 N+1번. 안에서 무엇이 바뀌어도 이 횟수에서 반드시 멈춘다
    for _ in range(MAX_TOOL_CALLS_PER_TASK + 1):
        # LLM은 지금까지 모은 근거·툴 기록·쓴 호출 수를 보고 판단한다
        now = {**state, "plan": plan, "current_task": idx, "evidence": evidence, "tool_log": tool_log,
               "counters": _bump(state, llm_calls=llm)}
        try:
            step, n = call_json("executor", "tool_choice", now, config, required=("action",))
        except LLMFailed as e:       # 형식 오류 소진·예산 소진: 여기까지 모은 근거는 살려서 마무리로
            upd = _fail_during(state, "executor", str(e), plan=plan)
            return {**upd, "evidence": evidence, "counters": _bump(state, llm_calls=llm + e.attempts, tool_calls=used)}
        llm += n
        if step["action"] != "call":                 # done (또는 알 수 없는 값) → 이 작업의 툴 호출 끝
            log.log("executor", "check", summary=f"작업{task['id']} 툴 호출 끝 ({used}회): {step.get('reason', '')}")
            break
        if used >= MAX_TOOL_CALLS_PER_TASK:
            log.log("executor", "limit_hit", "fail", f"작업{task['id']} 툴 호출 {MAX_TOOL_CALLS_PER_TASK}회 한도 → 여기까지 근거로 진행 (미완 표시)")
            limited.append(f"툴 호출 한도({MAX_TOOL_CALLS_PER_TASK}회)로 근거 일부만 수집")
            break
        used += 1
        name, args = step.get("tool"), step.get("args") or {}
        tool = TOOLS.get(name) if isinstance(name, str) else None
        if tool is None or not isinstance(args, dict):  # LLM이 이상한 툴 이름·인자를 줘도 죽지 않게 → 에러로 보여 줌
            err = "없는 툴" if tool is None else "args는 {이름: 값} 형태여야 함"
            log.log("executor", "tool_call", "fail", f"{err}: '{name}'", {"tool": name, "args": args})
            tool_log.append({"tool": name, "args": args, "error": err})
            continue
        call_args = {**args, "as_of": as_of}         # 기준일은 LLM이 아니라 코드가 요청 값으로 넣는다
        t0 = time.perf_counter()
        try:
            rows = tool(**call_args)
        except Exception as e:                       # 툴 에러로 실행이 죽지 않게. 에러는 다음 판단 때 LLM에 보여 준다
            log.log("executor", "tool_call", "fail", f"{name} 에러: {type(e).__name__}: {e}",
                    {"tool": name, "args": call_args, "error": str(e)})
            tool_log.append({"tool": name, "args": args, "error": f"{type(e).__name__}: {e}"})
            limited.append(f"{name} 툴 에러로 근거 일부 없음")
            continue
        ms = int((time.perf_counter() - t0) * 1000)
        for r in rows:
            evidence[r["id"]] = {**r, "tool": name, "task": task["id"]}
        tool_log.append({"tool": name, "args": args, "result_ids": [r["id"] for r in rows]})
        log.log("executor", "tool_call", summary=f"{name}({args}) → {len(rows)}건",
                data={"tool": name, "args": call_args, "returned": len(rows), "result_ids": [r["id"] for r in rows]}, duration_ms=ms)

    # 끝났다고 바로 completed로 두지 않는다. 진행 판단이 확정한다 (재계획이면 이 작업도 교체 대상).
    if limited:
        plan[idx]["limited"] = ", ".join(limited)
    return {"plan": plan, "current_task": idx, "evidence": evidence,
            "counters": _bump(state, llm_calls=llm, tool_calls=used)}


# =================================================================== 6. 진행 판단 (LLM 판단 + 코드 한도)
@logged_node("progress_judge")
def progress_judge(state: dict, config: RunnableConfig) -> dict:
    log = _log(state)
    try:
        out, n = call_json("progress_judge", "progress", state, config, required=("decision",))
    except LLMFailed as e:
        return _fail_during(state, "progress_judge", str(e), llm_calls=e.attempts)
    decision, reason = out["decision"], out.get("reason", "")
    plan = [dict(t) for t in state["plan"]]
    cur = plan[state["current_task"]]
    stop_reason = state.get("stop_reason")
    if decision not in ("continue", "replan", "finish"):
        decision, reason = "finish", f"알 수 없는 판단 '{decision}' → 종료"
    if decision == "replan" and state["replan_count"] >= MAX_REPLANS:
        log.log("progress_judge", "limit_hit", "fail", f"재계획 {MAX_REPLANS}회 한도 → 지금 작업은 실패로 두고 마무리")
        cur["status"], decision = "failed", "finish"
        stop_reason = f"재계획 한도({MAX_REPLANS}회) 도달: {reason}"
    elif decision != "replan":
        cur["status"] = "completed"              # replan이면 in_progress 그대로 → 재계획이 교체
    pending = any(t["status"] == "pending" for t in plan)
    if decision == "continue" and not pending:
        decision, reason = "finish", "남은 작업 없음"
    if decision == "finish" and pending and not stop_reason:
        stop_reason = f"남은 작업을 두고 종료: {reason}"
    log.log("progress_judge", "check", summary=f"{decision}: {reason} (작업{cur['id']} → {cur['status']})",
            data={"decision": decision, "task": cur["id"], "task_status": cur["status"], "llm": out})
    return {"plan": plan, "progress": {"decision": decision, "reason": reason}, "stop_reason": stop_reason,
            "counters": _bump(state, llm_calls=n)}


# =================================================================== 7. 재계획 (LLM 새 작업 + 코드 이력 정리)
@logged_node("replanner")
def replanner(state: dict, config: RunnableConfig) -> dict:
    try:
        out, n = call_json("replanner", "replan", state, config, required=("tasks",))
    except LLMFailed as e:
        return _fail_during(state, "replanner", str(e), llm_calls=e.attempts)
    plan = [dict(t) for t in state["plan"]]
    replaced = []
    for t in plan:                               # completed는 그대로, 진행 중·남은 작업은 replaced로 남긴다
        if t["status"] in ("pending", "in_progress"):
            t["status"] = "replaced"
            replaced.append(t["id"])
    next_id = max(t["id"] for t in plan) + 1     # LLM이 준 ID는 참조용, 실제 ID는 이어서 새로 매김
    id_map = {t.get("id", next_id + i): next_id + i for i, t in enumerate(out["tasks"])}
    new = [{**t, "id": next_id + i, "depends_on": [id_map.get(d, d) for d in t.get("depends_on", [])],
            "status": "pending"} for i, t in enumerate(out["tasks"])]
    _log(state).log("replanner", "check",
                    summary=f"재계획 {state['replan_count'] + 1}회째: 교체 {replaced} → 새 작업 {[t['id'] for t in new]}",
                    data={"replaced": replaced, "new_tasks": new})
    return {"plan": plan + new, "replan_count": state["replan_count"] + 1, "plan_source": "replanner",
            "current_task": None, "counters": _bump(state, llm_calls=n)}


# =================================================================== 8. 보고서 작성 (LLM 글 + 코드 이름표 채우기)
def _fmt(e: dict) -> str:
    if "value" in e:
        v = e["value"]
    elif "status" in e:
        v = f"{e['status']}({e['detail']})"
    elif "decision" in e:
        v = f"{e['decision']} — {e['reason']}"
    else:
        v = e["id"]
    src = e.get("source")
    return v + (f" [출처 {src['doc']} p.{src['page']}]" if src else "")


def _fill(text: str, evidence: dict, builtins: dict) -> str:
    def rep(m: re.Match) -> str:
        k = m.group(1)
        if k in builtins:
            return str(builtins[k])
        return _fmt(evidence[k]) if k in evidence else m.group(0)   # 모르는 이름표는 그대로 → 검사에서 걸림
    return TAG.sub(rep, text)


def _review_items(evidence: dict) -> list[dict]:
    return [{"id": e["id"], "fund": e["fund"], "type": e["type"], "detail": e["detail"], "source": e["source"]}
            for e in evidence.values() if e.get("status") == "불일치"]


@logged_node("composer")
def composer(state: dict, config: RunnableConfig) -> dict:
    queue, incomplete = _review_items(state["evidence"]), _incomplete(state)
    try:   # 마무리 단계라 전체 예산에서 뺀다 (예산이 바닥나도 모은 근거로 보고서는 써야 함)
        out, n = call_json("composer", "compose", state, config, required=("sections",), budgeted=False)
    except LLMFailed as e:
        _log(state).log("composer", "error", "fail", f"{e} → 자동 양식으로 작성")
        return {"draft": _fallback_draft(state, incomplete), "review_queue": queue,
                "counters": _bump(state, llm_calls=e.attempts)}
    raw = out["sections"]
    filled = {k: _fill(v, state["evidence"], {"n_review": len(queue)}) for k, v in raw.items()}
    if incomplete:                               # 미완 표시는 코드가 붙인다 (LLM 글 아님)
        filled["incomplete"] = _incomplete_text(incomplete, state.get("stop_reason"))
    _log(state).log("composer", "check",
                    summary=f"초안 {len(raw)}개 절, 검토 대기 {len(queue)}건, 미완 {len(incomplete)}건",
                    data={"sections": sorted(raw), "review_ids": [q["id"] for q in queue],
                          "incomplete": [t["id"] for t in incomplete]})
    return {"draft": {"raw": raw, "filled": filled, "refs": _section_refs(raw), "fallback": False,
                      "incomplete": incomplete},
            "review_queue": queue, "counters": _bump(state, llm_calls=n)}


def _section_refs(raw: dict) -> dict[str, list[str]]:
    """절마다 인용한 참조 ID (= DB의 보고서 절 ↔ 사용 근거 연결). 정정 때 바뀐 근거를 쓴 절만 찾는 데 쓴다."""
    return {sec: sorted({t for t in TAG.findall(text) if t not in BUILTIN_TAGS}) for sec, text in raw.items()}


def _incomplete(state: dict) -> list[dict]:
    """끝내지 못한 작업 + 툴 한도로 근거를 일부만 모은 작업 (replaced는 대체됐으니 제외)."""
    return [{"id": t["id"], "goal": t["goal"], "sections": t["sections"], "status": t["status"],
             "why": t.get("limited", "")}
            for t in state["plan"]
            if t["status"] in ("pending", "in_progress", "failed") or (t["status"] == "completed" and t.get("limited"))]


def _incomplete_text(incomplete: list[dict], stop_reason: str | None) -> str:
    parts = [f"작업{t['id']} {t['goal']}" + (f"({t['why']})" if t["why"] else "") for t in incomplete]
    return "[미완] " + "; ".join(parts) + (f" — {stop_reason}" if stop_reason else "")


# =================================================================== 9. 보고서 검사 (코드)
def _report_violations(state: dict) -> list[str]:
    raw = state["draft"]["raw"]
    required = [s["id"] for s in state["template"]["sections"] if s["required"]]
    v = [f"필수 절 빠짐 '{s}'" for s in required if not raw.get(s, "").strip()]
    known = set(state["evidence"]) | BUILTIN_TAGS
    for sec, text in raw.items():
        if unknown := [t for t in TAG.findall(text) if t not in known]:
            v.append(f"{sec}: 모르는 이름표 {unknown}")
        plain = TAG.sub("", text)
        if nums := [m.group(0) for m in RAW_NUMBER.finditer(plain)]:
            v.append(f"{sec}: 이름표 없이 쓴 숫자 {nums}")
        if words := STATUS_WORDS.findall(plain):
            v.append(f"{sec}: 상태 단정 표현 {words}")
    return v


def _fallback_draft(state: dict, incomplete: list[dict]) -> dict:
    """재작성도 실패하면 코드가 정해진 양식으로 근거만 나열한다."""
    sec_of_task = {t["id"]: t["sections"] for t in state["plan"]}
    filled, refs = {}, {}
    for s in state["template"]["sections"]:
        used = [e for e in state["evidence"].values() if s["id"] in sec_of_task.get(e["task"], [])]
        filled[s["id"]] = f"[자동 양식] {s['title']}: " + ("; ".join(_fmt(e) for e in used) or "근거 없음")
        refs[s["id"]] = sorted(e["id"] for e in used)
    if incomplete:
        filled["incomplete"] = _incomplete_text(incomplete, state.get("stop_reason"))
    return {"raw": {}, "filled": filled, "refs": refs, "fallback": True, "incomplete": incomplete}


@logged_node("report_checker")
def report_checker(state: dict, config: RunnableConfig) -> dict:
    log = _log(state)
    if state["draft"]["fallback"]:                 # 코드가 만든 자동 양식은 검사할 LLM 글이 없다
        log.log("report_checker", "check", summary="자동 양식 → 검사 생략")
        return {"check": {"ok": True, "violations": [], "next": "human_review"}, "status": "waiting_review"}
    v = _report_violations(state)
    log.log("report_checker", "check", "fail" if v else "ok", "; ".join(v) if v else "위반 없음", {"violations": v})
    after, status = "human_review", "waiting_review"   # 기획서: 초안은 항상 담당자 확인으로 넘긴다
    if not v:
        return {"check": {"ok": True, "violations": [], "next": after}, "status": status}
    if state["rewrite_count"] < REPORT_REWRITE:
        return {"check": {"ok": False, "violations": v, "next": "composer"}, "rewrite_count": state["rewrite_count"] + 1}
    log.log("report_checker", "limit_hit", "fail", f"재작성 {REPORT_REWRITE}회 소진 → 자동 양식으로 대체")
    return {"check": {"ok": False, "violations": v, "next": after}, "status": status,
            "draft": _fallback_draft(state, state["draft"].get("incomplete", []))}


# =================================================================== 10. 담당자 확인 (사람, interrupt)
def _review_error(resp: Any) -> str | None:
    if not isinstance(resp, dict):
        return "응답은 {action, reason, reviewer} 형태여야 함"
    if resp.get("action") not in ("approve", "reject"):
        return f"action은 approve 또는 reject (받은 값: {resp.get('action')!r})"
    if resp["action"] == "reject" and not resp.get("reason"):
        return "반려는 사유가 필요함"
    return None


@logged_node("human_review")
def human_review(state: dict, config: RunnableConfig) -> dict:
    log = _log(state)
    payload = {
        "run_id": state["run_id"],
        "items": state["review_queue"],          # 검토 대기 항목 (근거·출처 포함) → 화면에서 강조
        "incomplete": state["draft"].get("incomplete", []),   # 미완 작업 → 화면에서 강조
        "draft": state["draft"]["filled"],
        "error": state.get("review_error"),      # 직전 응답이 잘못됐으면 이유를 같이 보여줌
        "expected": {"action": "approve | reject", "reason": "반려 시 필수", "reviewer": "이름"},
    }
    resp = interrupt(payload)                    # 여기서 멈추고, 재개되면 이 노드를 처음부터 다시 실행
    if err := _review_error(resp):
        log.log("human_review", "check", "fail", f"잘못된 응답 → 다시 요청: {err}", {"response": resp})
        return {"review": None, "review_error": err}
    log.log("human_review", "check", summary=f"{resp['action']} by {resp.get('reviewer', '?')}", data={"response": resp})
    return {"review": resp, "review_error": None}


# =================================================================== 11. 기록 확정 (코드)
@logged_node("commit")
def commit(state: dict, config: RunnableConfig) -> dict:
    if state.get("notice"):
        # 작성 보류(범위 밖·정보 부족) 또는 실행 전 실패: 보고서 없이 사유만 기록
        status = "failed" if state.get("aborted") else "held"
    elif state.get("review") and state["review"]["action"] == "reject":
        status = "rejected"
    elif state["draft"].get("incomplete"):
        status = "partial"                       # 한도·재계획 실패로 일부만 작성
    else:
        status = "completed"
    record = {
        "run_id": state["run_id"], "status": status, "request": state["request"],
        "notice": state.get("notice"),
        "report": (state.get("draft") or {}).get("filled"), "fallback": (state.get("draft") or {}).get("fallback"),
        "section_refs": (state.get("draft") or {}).get("refs", {}),   # 절 → 인용한 참조 ID (DB 보고서 절 ↔ 사용 근거로)
        "incomplete": (state.get("draft") or {}).get("incomplete", []), "stop_reason": state.get("stop_reason"),
        "review_items": state["review_queue"],   # 승인해도 불일치 기록은 지우지 않는다
        "review": state.get("review"), "plan": state["plan"], "counters": state["counters"],
    }
    path = LOG_DIR / f"{state['run_id']}_report.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    c = state["counters"]
    _log(state).log("commit", "run_end", summary=f"상태={status} | LLM {c['llm_calls']}회 · 툴 {c['tool_calls']}회 | {path.name}",
                    data={"status": status, **c, "report_file": path.name})
    return {"status": status}


NODES = {
    "start": start, "understand": understand, "planner": planner, "plan_checker": plan_checker,
    "executor": executor, "progress_judge": progress_judge, "replanner": replanner,
    "composer": composer, "report_checker": report_checker, "human_review": human_review, "commit": commit,
}
