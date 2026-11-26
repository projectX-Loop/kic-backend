"""노드별 LLM 입력 (노드마다 LLM에 보내는 것).

LLM에는 State 전체가 아니라 노드마다 필요한 것만 보낸다.
  - 토큰 절약
  - 관계없는 근거를 보고 다른 펀드·분기 값을 끌어오는 것 방지
  - 보고서 작성에는 숫자 값을 아예 안 보낸다 → 이름표 말고는 숫자를 쓸 수 없다

지금(틀): 가짜 LLM은 여전히 State 전체를 받는다. 여기서 만든 입력은 llm_call 로그에 "무엇을 보냈는지"만 남긴다.
메시지 조립: llm.call_json이 이 입력(user)과 prompts.py 지시문(system)을 합치고, _raw_call이 보낸다.
  툴 결과(문서에서 온 글)는 user 쪽 데이터로만 들어간다 (문서 속 지시문을 따르지 않게).
"""
from __future__ import annotations

from typing import Any

from .config import MAX_TASKS, MAX_TOOL_CALLS_PER_TASK
from .fakes import TEMPLATES
from .tools import TOOL_GUIDE   # 툴 설명은 tools.py 한곳에. 작업 계획은 이걸 보고 툴로 할 수 있는 작업만 짜고, 실행기는 툴을 고른다

# 보고서 작성에 보내는 근거 칸: 무엇에 대한 근거인지만, 값·상세(숫자 포함)는 뺀다
_REF_FIELDS = ("id", "tool", "task", "fund", "name", "type", "pair", "of", "doc", "section")


def _task_brief(t: dict) -> dict:
    # limited = 툴 한도·에러로 근거를 일부만 모은 사유 (있을 때만)
    return {k: t[k] for k in ("id", "goal", "sections", "targets", "depends_on", "done_criteria", "status", "limited")
            if k in t}


def _current(state: dict) -> dict | None:
    idx = state.get("current_task")
    return state["plan"][idx] if idx is not None else None


def _with_deps(plan: list[dict], task_id: int) -> set[int]:
    """작업 하나 + 그 작업이 의존하는 작업들(거슬러 올라가며 전부)."""
    by_id = {t["id"]: t for t in plan}
    seen, stack = set(), [task_id]
    while stack:
        tid = stack.pop()
        if tid in seen or tid not in by_id:
            continue
        seen.add(tid)
        stack.extend(by_id[tid].get("depends_on", []))
    return seen


def _evidence_for(state: dict, task_ids: set[int]) -> list[dict]:
    return [e for e in state.get("evidence", {}).values() if e.get("task") in task_ids]


def _incomplete_sections(state: dict) -> list[str]:
    """끝내지 못했거나 근거를 일부만 모은 작업의 절 (nodes._incomplete와 같은 기준)."""
    bad = [t for t in state["plan"]
           if t["status"] in ("pending", "in_progress", "failed") or (t["status"] == "completed" and t.get("limited"))]
    return sorted({s for t in bad for s in t["sections"]})


# ------------------------------------------------------------------ 노드별 입력
def build_understand_context(state: dict) -> dict[str, Any]:
    """양식·요청 이해: 요청 문장과 고를 수 있는 양식 목록. 근거·계획은 아직 없다."""
    req = state["request"]
    return {"request_text": req.get("request_text", ""),
            "request": {k: req.get(k) for k in ("template_id", "funds", "as_of")},
            "templates": [{"id": t["template_id"], "name": t["name"]} for t in TEMPLATES.values()]}


def build_planner_context(state: dict) -> dict[str, Any]:
    """작업 계획: 요청 해석 결과, 양식 절, 쓸 수 있는 툴, 한도. 다시 계획이면 지난 계획의 위반 사유도 (고칠 수 있게).

    툴 목록을 주는 이유: 툴로 할 수 없는 작업(외부 검색 등)을 계획하지 않게.
    """
    req = state["request"]
    return {"spec": state.get("spec"),
            "funds": req["funds"], "as_of": req["as_of"],
            "sections": [{k: s[k] for k in ("id", "title", "required")} for s in state["template"]["sections"]],
            "tools": TOOL_GUIDE,
            "max_tasks": MAX_TASKS,
            "previous_violations": (state.get("plan_check") or {}).get("violations", [])}


def build_executor_context(state: dict) -> dict[str, Any]:
    """실행기 (툴 루프 한 걸음마다): 지금 작업 하나, 툴 설명, 이 작업에서 부른 툴 기록(에러 포함)과 모은 근거,
    의존 작업이 모은 근거, 남은 툴 호출 수.

    툴 기록을 주는 이유: 같은 툴을 반복하지 않고, 에러가 났으면 인자를 고쳐 다시 부르게.
    의존 작업 근거를 주는 이유: 이미 조회한 지표를 다시 조회하지 않게.
    관계없는 작업과 그 근거는 안 보낸다.
    """
    task = _current(state)
    deps = _with_deps(state["plan"], task["id"]) - {task["id"]}
    tool_log = state.get("tool_log", [])
    return {"task": _task_brief(task),
            "tools": TOOL_GUIDE,
            "tool_log": tool_log,                                    # 툴 부를 때마다 늘어남
            "results_so_far": _evidence_for(state, {task["id"]}),
            "dependency_evidence": _evidence_for(state, deps),
            "calls_left": max(MAX_TOOL_CALLS_PER_TASK - len(tool_log), 0)}


def build_progress_context(state: dict) -> dict[str, Any]:
    """진행 판단: 지금 작업(근거를 일부만 모았으면 limited 사유 포함), 그 작업과 의존 작업의 근거만, 남은 작업 목록."""
    task = _current(state)
    return {"task": _task_brief(task),
            "evidence": _evidence_for(state, _with_deps(state["plan"], task["id"])),
            "pending": [_task_brief(t) for t in state["plan"] if t["status"] == "pending"]}


def build_replanner_context(state: dict) -> dict[str, Any]:
    """재계획: 끝난 작업, 교체될 작업과 그 작업이 본 근거, 이전 남은 작업, 재계획 사유, 아직 안 채운 절.

    교체될 작업의 근거를 주는 이유: 무엇이 부족했는지 봐야 제대로 고친다.
    끝난 작업의 근거가 필요한 새 작업은 다시 조회하지 말고 그 작업에 의존하게 한다 (프롬프트 규칙).
    """
    done = [t for t in state["plan"] if t["status"] == "completed"]
    covered = {s for t in done for s in t["sections"]}
    cur = _current(state)
    return {"completed": [_task_brief(t) for t in done],
            "replacing": _task_brief(cur) if cur else None,
            "evidence_seen": _evidence_for(state, _with_deps(state["plan"], cur["id"])) if cur else [],
            "old_remaining": [_task_brief(t) for t in state["plan"] if t["status"] == "pending"],
            "reason": (state.get("progress") or {}).get("reason", ""),
            "uncovered_sections": [s["id"] for s in state["template"]["sections"] if s["id"] not in covered],
            "funds": state["request"]["funds"], "as_of": state["request"]["as_of"],
            "max_tasks": MAX_TASKS,
            # 새 작업의 첫 ID. 없으면 LLM이 1부터 매겨 끝난 작업 ID와 겹치고, 의존 번호가 엉뚱하게 바뀔 수 있다
            "next_id": max((t["id"] for t in state["plan"]), default=0) + 1}


def build_composer_context(state: dict) -> dict[str, Any]:
    """보고서 작성: 양식과 참조 ID·종류만 (숫자 값 없음), 미완 절. 다시 쓰기면 보고서 검사 위반 사유도.

    미완 절을 주는 이유: [미완] 표시는 코드가 붙이지만, LLM이 모르면 근거 없는 절을 다 된 것처럼 쓴다.
    """
    refs = [{k: e[k] for k in _REF_FIELDS if k in e} for e in state.get("evidence", {}).values()]
    return {"sections": [{k: s[k] for k in ("id", "title", "required")} for s in state["template"]["sections"]],
            "evidence_refs": refs,
            "n_review": sum(1 for e in state.get("evidence", {}).values() if e.get("status") == "불일치"),
            "incomplete_sections": _incomplete_sections(state),
            "previous_violations": (state.get("check") or {}).get("violations", [])}


BUILDERS = {
    "understand": build_understand_context,
    "plan": build_planner_context,
    "tool_choice": build_executor_context,
    "progress": build_progress_context,
    "replan": build_replanner_context,
    "compose": build_composer_context,
}


def build_context(purpose: str, state: dict) -> dict[str, Any]:
    return BUILDERS[purpose](state)
