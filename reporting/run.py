"""시나리오 실행기.

  python -m reporting.run                     # normal 한 번
  python -m reporting.run --scenario reject   # 시나리오 하나
  python -m reporting.run --all               # 전부
  python -m reporting.run --ask               # 담당자 확인을 직접 입력 (approve / reject 사유)

로그: logs/{run_id}.jsonl, 최종 기록: logs/{run_id}_report.json (실행한 위치 기준, REPORTING_LOG_DIR로 변경)
"""
from __future__ import annotations

import argparse
from datetime import datetime

from langgraph.types import Command

from .graph import build_graph
from .runlog import get_logger

APPROVE = {"action": "approve", "reason": "", "reviewer": "담당자"}
REJECT = {"action": "reject", "reason": "C 펀드 TVPI 원문 재확인 필요", "reviewer": "담당자"}
BASE = {"text": "2025년 2분기 A·B·C 펀드 분기 운용성과 보고서 작성", "funds": ["A", "B", "C"], "reviews": [APPROVE]}

# 시나리오: 흐름도의 갈림길을 하나씩 타 보게 만든 것
SCENARIOS = {
    "normal":       {**BASE},                                                   # 정상 → 담당자 승인
    "no_issue":     {**BASE, "text": "2025년 2분기 A·B 펀드 분기 운용성과 보고서 작성", "funds": ["A", "B"]},  # 검토 대기 없어도 담당자 확인
    "missing_info": {**BASE, "as_of": None, "reviews": []},                   # 기준일 없는 요청 → 코드가 바로 작성 보류
    "out_of_scope": {"text": "B 펀드에 추가 투자해도 될까?", "funds": ["B"], "reviews": []},  # 범위 밖 → 작성 보류
    "json_retry":   {**BASE},                                                   # LLM 형식 오류 1회 → 재시도
    "plan_retry":   {**BASE},                                                   # 계획 검사 위반 → 1회 재계획 → 통과
    "plan_fail":    {**BASE, "reviews": []},                                    # 다시 짜도 위반 → 작성 보류
    "replan":       {**BASE},                                                   # 진행 판단이 재계획 선택 → 완료 보존·나머지 교체
    "replan_fail":  {**BASE},                                    # 재계획이 검사에 걸림 → 모은 근거로 작성·미완 표시
    "tool_limit":   {**BASE},                                                   # 툴 호출 한도 → 미완 표시
    "rewrite":      {**BASE},                                                   # 보고서 검사 위반 → 1회 재작성
    "reject":       {**BASE, "reviews": [REJECT]},                              # 담당자 반려
    "review_retry": {**BASE, "reviews": [{"action": "ok"}, APPROVE]},        # 잘못된 응답 → 다시 요청
    # ---- 실패 경로
    "llm_fail":     {**BASE, "reviews": []},                                    # 작업 계획 LLM 형식 오류 3회 → 실패 기록
    "tool_error":   {**BASE},                                                   # 툴 에러 → 죽지 않고 계속, 미완 표시
    "budget":       {**BASE, "max_llm_calls": 5},                               # 전체 LLM 예산 소진 → 모은 근거로 작성·미완
    "wrong_period": {**BASE},                                                   # 계획이 다른 분기 → 계획 검사에서 걸림 → 다시
    "cycle":        {**BASE},                                                   # 작업 순환 의존 → 계획 검사에서 걸림 → 다시
}


def _ask(payload: dict) -> dict:
    print("\n── 담당자 확인 ──")
    if payload.get("error"):
        print(f"  (직전 응답 오류: {payload['error']})")
    for it in payload["items"]:
        print(f"  · {it['fund']} {it['type']}: {it['detail']}  [{it['source']['doc']} p.{it['source']['page']}]")
    ans = input("  approve / reject 사유 > ").strip()
    if ans.startswith("reject"):
        return {"action": "reject", "reason": ans[6:].strip(), "reviewer": "사용자"}
    return {"action": ans, "reason": "", "reviewer": "사용자"}


def run(name: str, ask: bool = False) -> str:
    sc = SCENARIOS[name]
    run_id = f"{datetime.now():%m%d-%H%M%S}-{name}"
    print(f"\n===== {name}  (run_id={run_id}) =====")
    app = build_graph()
    cfg = {"configurable": {"thread_id": run_id, "scenario": name}}
    if "max_llm_calls" in sc:
        cfg["configurable"]["max_llm_calls"] = sc["max_llm_calls"]
    init = {"run_id": run_id, "request": {"template_id": "quarterly_performance", "request_text": sc["text"],
                                          "funds": sc["funds"], "as_of": sc.get("as_of", "2025Q2")}}
    result = app.invoke(init, cfg)
    answers = iter(sc["reviews"])
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        resp = _ask(payload) if ask else next(answers)
        get_logger(run_id).log("human_review", "resume", summary=f"담당자 응답 {resp}", data={"response": resp})
        result = app.invoke(Command(resume=resp), cfg)
    return result["status"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    p.add_argument("--all", action="store_true")
    p.add_argument("--ask", action="store_true")
    a = p.parse_args()
    names = list(SCENARIOS) if a.all else [a.scenario]
    results = {n: run(n, a.ask) for n in names}
    print("\n===== 결과 =====")
    for n, s in results.items():
        print(f"  {n:<13} {s}")


if __name__ == "__main__":
    main()
