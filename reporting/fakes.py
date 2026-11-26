"""가짜 양식·데이터·툴·LLM. 실제 LLM·툴·데이터가 붙기 전까지 흐름과 로그를 확인하는 용도.

※ 이 파일의 펀드·지표·검증 값은 모두 **합성(SYNTHETIC)** 이다. 실제 PDF 추출 결과가 아니다.

- 툴 5개는 데모 시나리오의 A·B·C 펀드 고정 데이터를 돌려준다. 툴 이름·칸은 tools.py를 따른다.
  진짜 툴은 tools.py에 채운다.
- 가짜 LLM은 시나리오별로 정해 둔 응답을 돌려준다. 진짜 LLM을 붙여도 갈림길 테스트용으로 남긴다.
"""
from __future__ import annotations

from typing import Any

from .config import MAX_TASKS
from .tools import TOOL_SCHEMAS

# ------------------------------------------------------------------ 양식 (MVP 1종)
TEMPLATES: dict[str, dict[str, Any]] = {
    "quarterly_performance": {
        "template_id": "quarterly_performance",
        "name": "분기 운용성과 보고서",
        "sections": [
            {"id": "performance", "title": "펀드별 성과", "required": True},
            {"id": "comparison", "title": "기준 비교", "required": True},
            {"id": "issues", "title": "검토 필요 항목", "required": True},
        ],
    }
}

# ------------------------------------------------------------------ 합성 데이터 (SYNTHETIC, A·B·C 펀드, 2025Q2 — 실제 PDF 아님)
SYNTHETIC = True
_SRC = lambda doc, page: {"doc": doc, "version": 1, "page": page}  # noqa: E731
METRICS = {
    "m_A_net_irr": {"fund": "A", "period": "2025Q2", "name": "net_irr", "value": "14.2%", "basis": "net", "level": "fund", "currency": "USD", "source": _SRC("A_10Q_2025Q2", 41)},
    "m_B_net_irr": {"fund": "B", "period": "2025Q2", "name": "net_irr", "value": "11.8%", "basis": "net", "level": "fund", "currency": "EUR", "source": _SRC("B_10Q_2025Q2", 37)},
    "m_C_tvpi":    {"fund": "C", "period": "2025Q2", "name": "tvpi", "value": "1.62x", "basis": "net", "level": "LP", "currency": "USD", "source": _SRC("OPERF_2025Q2", 12)},
    # 직전 분기 값: 기준일로 걸러져서 2025Q2 보고서에는 들어오면 안 된다
    "m_A_net_irr_q1": {"fund": "A", "period": "2025Q1", "name": "net_irr", "value": "13.9%", "basis": "net", "level": "fund", "currency": "USD", "source": _SRC("A_10Q_2025Q1", 39)},
}
VERIFICATIONS = {
    "v_C_tvpi": {"fund": "C", "period": "2025Q2", "type": "TVPI 재계산", "status": "불일치", "detail": "재계산 1.58x vs 보고 1.62x", "source": _SRC("OPERF_2025Q2", 12)},
}


# ------------------------------------------------------------------ 툴 5개 (조회 전용)
# 모든 툴은 기준일(as_of)을 받는다. 실행기가 LLM 인자와 상관없이 요청 기준일을 넣는다 (분기 섞임 방지).
def get_metrics(funds: list[str], as_of: str, **_: Any) -> list[dict]:
    return [{"id": k, **v} for k, v in METRICS.items() if v["fund"] in funds and v["period"] == as_of]


def get_verification(funds: list[str], as_of: str, **_: Any) -> list[dict]:
    return [{"id": k, **v} for k, v in VERIFICATIONS.items() if v["fund"] in funds and v["period"] == as_of]


def judge_comparability(metric_ids: list[str], as_of: str, **_: Any) -> list[dict]:
    # 실제로는 정의 칸으로 코드가 먼저 판정, 각주만 LLM. 지금은 고정 결과.
    return [{"id": "j_A_B_irr", "pair": metric_ids, "decision": "조건부",
             "reason": "순수익·펀드 기준은 같고 통화만 다름(USD/EUR) → 재계산 시 비교"}]


def search_rules(query: str, **_: Any) -> list[dict]:   # 규정은 기간과 무관
    return [{"id": "r_ilpa_irr", "doc": "ILPA Performance Template", "section": "IRR 정의", "text": "(원문 생략)"}]


def get_lineage(item_id: str, as_of: str, **_: Any) -> list[dict]:
    src = (METRICS.get(item_id) or VERIFICATIONS.get(item_id) or {}).get("source", {})
    return [{"id": f"l_{item_id}", "of": item_id, "source": src}]


# 툴 이름 → 함수: 이름은 tools.TOOL_SCHEMAS 한곳에서. 가짜 함수가 빠지면 여기서 바로 에러
TOOLS = {name: globals()[s["fn"]] for name, s in TOOL_SCHEMAS.items()}


# ------------------------------------------------------------------ 가짜 LLM
class BadJSON(Exception):
    """LLM이 형식이 틀린 응답을 낸 상황을 흉내낸다."""


def fake_llm(purpose: str, scenario: str, state: dict, attempt: int) -> dict:
    """노드 목적(purpose)과 시나리오에 따라 정해 둔 응답을 돌려준다."""
    funds = state.get("request", {}).get("funds", [])

    if purpose == "understand":
        if scenario == "json_retry" and attempt == 1:
            raise BadJSON("응답이 JSON이 아님")
        if scenario == "out_of_scope":
            return {"template_id": None, "funds": funds, "out_of_scope": True,
                    "missing_info": [], "reason": "투자 추천 요청은 범위 밖"}
        return {"template_id": "quarterly_performance", "funds": funds,
                "out_of_scope": False, "missing_info": []}

    as_of = state.get("request", {}).get("as_of")

    if purpose == "plan":
        if scenario == "llm_fail":
            raise BadJSON("응답이 JSON이 아님 (계속 실패)")
        first = state.get("plan_retry", 0) == 0
        too_many = (scenario == "plan_retry" and first) or scenario == "plan_fail"
        tasks = [
            {"id": 1, "goal": "A·B IRR 비교표", "sections": ["performance", "comparison"], "period": as_of,
             "targets": [f for f in funds if f in ("A", "B")], "depends_on": [], "done_criteria": ["metric", "judgment"]},
            {"id": 2, "goal": "이상 징후 요약", "sections": ["issues"], "period": as_of,
             "targets": funds, "depends_on": [], "done_criteria": ["verification"]},
        ]
        if scenario == "wrong_period" and first:   # 직전 분기로 잘못 계획 → 계획 검사에서 걸림
            tasks[1]["period"] = "2025Q1"
        if scenario == "cycle" and first:          # 서로 의존(순환) → 계획 검사에서 걸림
            tasks[0]["depends_on"], tasks[1]["depends_on"] = [2], [1]
        if too_many:  # 작업 수 한도(MAX_TASKS) 위반을 일부러 만든다
            tasks += [{"id": i, "goal": f"추가 작업 {i}", "sections": [], "targets": ["A"], "period": as_of,
                       "depends_on": [], "done_criteria": []} for i in range(3, MAX_TASKS + 2)]
        return {"tasks": tasks}

    if purpose == "tool_choice":  # 실행기 툴 루프: 정해 둔 순서에서 이번 차례 툴 하나, 다 불렀으면 done
        task = state["plan"][state["current_task"]]
        if "performance" in task["sections"]:
            calls = [("지표 조회", {"funds": task["targets"]}),
                     ("비교 판정", {"metric_ids": ["m_A_net_irr", "m_B_net_irr"]}),
                     ("규정 검색", {"query": "IRR 정의"})]
            if scenario == "tool_error":           # 인자를 틀리게 줘서 툴이 에러를 내게 만든다
                calls.insert(1, ("검증 결과 조회", {"fundz": task["targets"]}))
        else:
            calls = [("검증 결과 조회", {"funds": task["targets"]}),
                     ("지표 조회", {"funds": task["targets"]})]
            if "C" in task["targets"]:
                calls.append(("계보 조회", {"item_id": "v_C_tvpi"}))
        if scenario == "tool_limit":
            calls = calls * 3  # 툴 호출 한도(6)를 넘기게 만든다
        step = len(state.get("tool_log", []))       # 이 작업에서 이미 부른 수
        if step >= len(calls):
            return {"action": "done", "reason": "필요한 근거 다 모음"}
        name, args = calls[step]
        return {"action": "call", "tool": name, "args": args}

    if purpose == "progress":
        pending = [t for t in state["plan"] if t["status"] == "pending"]
        if scenario in ("replan", "replan_fail") and state.get("replan_count", 0) == 0:
            return {"decision": "replan", "reason": "A·B 통화가 달라 재계산 근거 필요 → 남은 작업 재조정"}
        return ({"decision": "continue", "reason": "다음 작업 진행"} if pending
                else {"decision": "finish", "reason": "모든 작업 완료"})

    if purpose == "replan":  # 남은 작업만 새로 낸다 (끝난 작업은 코드가 보존)
        new = [{"id": 1, "goal": "A·B IRR 비교표(통화 재계산 포함)", "sections": ["performance", "comparison"], "period": as_of,
                "targets": [f for f in funds if f in ("A", "B")], "depends_on": [], "done_criteria": ["metric", "judgment"]},
               {"id": 2, "goal": "이상 징후 요약", "sections": ["issues"], "targets": funds, "period": as_of,
                "depends_on": [], "done_criteria": ["verification"]}]
        if scenario == "replan_fail":  # 재계획이 작업 수 한도를 넘기게 만든다
            new += [{"id": i, "goal": f"추가 작업 {i}", "sections": [], "targets": ["A"], "period": as_of,
                     "depends_on": [], "done_criteria": []} for i in range(3, MAX_TASKS + 2)]
        return {"tasks": new}

    if purpose == "compose":
        # LLM은 값 대신 이름표 {참조ID}만 쓴다. 값·출처는 코드가 채운다.
        raw_number = scenario == "rewrite" and state.get("rewrite_count", 0) == 0
        issues = "검토 필요 항목은 {n_review}건입니다."
        if "v_C_tvpi" in state.get("evidence", {}):
            issues += " C 펀드 TVPI 검증 결과: {v_C_tvpi}"
        return {"sections": {
            "performance": "A 펀드 Net IRR은 {m_A_net_irr}, B 펀드 Net IRR은 "
                           + ("11.8%" if raw_number else "{m_B_net_irr}") + "입니다.",
            "comparison": "A·B IRR 비교 판정: {j_A_B_irr}",
            "issues": issues,
        }}

    raise ValueError(f"unknown purpose: {purpose}")
