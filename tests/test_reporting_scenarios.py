"""보고서 작성 에이전트 시나리오 테스트.

합성 데이터와 가짜 LLM으로 그래프 갈림길을 하나씩 실행하고 최종 상태를 확인한다.
DB 연결과 실제 LLM 호출은 없다. `reporting` extra(LangGraph)가 없으면 건너뛴다.
"""
import pytest

pytest.importorskip("langgraph")

from reporting import nodes, runlog  # noqa: E402
from reporting.run import SCENARIOS, run  # noqa: E402

EXPECTED_STATUS = {
    "normal": "completed",
    "no_issue": "completed",
    "missing_info": "held",
    "out_of_scope": "held",
    "json_retry": "completed",
    "plan_retry": "completed",
    "plan_fail": "held",
    "replan": "completed",
    "replan_fail": "partial",
    "tool_limit": "partial",
    "rewrite": "completed",
    "reject": "rejected",
    "review_retry": "completed",
    "llm_fail": "failed",
    "tool_error": "partial",
    "budget": "partial",
    "wrong_period": "completed",
    "cycle": "completed",
}


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "LOG_DIR", tmp_path)
    monkeypatch.setattr(nodes, "LOG_DIR", tmp_path)


def test_every_scenario_has_expected_status() -> None:
    assert set(EXPECTED_STATUS) == set(SCENARIOS)


@pytest.mark.parametrize(("scenario", "expected"), EXPECTED_STATUS.items())
def test_scenario_final_status(scenario: str, expected: str) -> None:
    assert run(scenario) == expected
