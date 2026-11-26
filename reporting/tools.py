"""툴 5개 정의 (틀). 칸 이름은 팀 DB·추출 데이터와 맞추는 출발점.

지금: 설명(TOOL_GUIDE)만 연결됨 → context.py가 작업 계획·실행기 입력에 씀.
      툴 함수는 몸통이 비어 있고, 실행은 계속 fakes.TOOLS가 한다.
나중에: 함수 안을 orchestrator가 넘겨주는 조회 함수(문서 판본 고정) 호출로 채우고,
      nodes.py의 `from .fakes import TOOLS` 대신 이 툴을 쓴다.
      (선택) 실행기를 모델 툴 호출 기능으로 바꾸면 function_schemas()를 tools=로 넘긴다. 지금은 JSON 한 걸음씩.

모든 툴: 조회 전용(쓰기 없음). 행마다 `id`(지표·검증 결과 등의 참조 ID)가 보고서 이름표 {id}.
  ※ 팀 용어에서 "근거 ID"는 원문 위치(원문 근거)의 ID다. 이름표로 쓰는 이 ID와 구분해 "참조 ID"라 부른다.
기준일(as_of)은 LLM이 고르지 않고 실행기가 요청 값으로 넣는다 (규정 검색은 기간과 무관).
"""
from __future__ import annotations

from typing import Any

# source = {doc, version, page} → DB의 Document·SourceBlock
_SOURCE = "source"

# 툴 이름 → 정의. args = LLM이 고르는 인자(as_of 제외), returns = 행마다 돌려줄 칸, db = 계보 테이블
# 툴 스키마 초안
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "지표 조회": {
        "fn": "get_metrics",
        "args": {"funds": "펀드 목록"},
        "summary": "펀드별 지표 값·기준(gross/net)·단위·통화·출처",
        "returns": ["id", "fund", "period", "name", "value", "basis", "level", "currency", _SOURCE],
        "db": "Metric",
    },
    "검증 결과 조회": {
        "fn": "get_verification",
        "args": {"funds": "펀드 목록"},
        "summary": "검증 종류·일치/불일치·상세·출처",
        "returns": ["id", "fund", "period", "type", "status", "detail", _SOURCE],
        "db": "ValidationResult",
    },
    "비교 판정": {
        "fn": "judge_comparability",
        "args": {"metric_ids": "비교할 지표 ID 목록"},
        "summary": "비교 가능/조건부/불가와 사유",
        "returns": ["id", "pair", "decision", "reason"],
        "db": "ValidationResult? (확인 필요)",
    },
    "규정 검색": {
        "fn": "search_rules",
        "args": {"query": "검색어"},
        "summary": "ILPA 등 규정 문서의 해당 절",
        "returns": ["id", "doc", "section", "text"],
        "db": "규정 문서 (pgvector 검색)",
    },
    "계보 조회": {
        "fn": "get_lineage",
        "args": {"item_id": "지표·검증 결과 ID"},
        "summary": "그 항목의 원본 문서·쪽",
        "returns": ["id", "of", _SOURCE],
        "db": "Document → SourceBlock",
    },
}

# 작업 계획·실행기 입력용 요약 (context.py가 씀)
TOOL_GUIDE = {name: {"args": s["args"], "returns": s["summary"]} for name, s in TOOL_SCHEMAS.items()}


def function_schemas() -> list[dict]:
    """(아직 안 씀) 실행기를 모델 툴 호출 기능으로 바꿀 때 tools=에 넘길 형식."""
    return [{"type": "function", "function": {
                "name": s["fn"], "description": f"{name}: {s['summary']}",
                "parameters": {"type": "object", "required": list(s["args"]),
                               "properties": {a: {"description": d} for a, d in s["args"].items()}}}}
            for name, s in TOOL_SCHEMAS.items()]


# ------------------------------------------------------------------ 진짜 툴 (몸통은 조회 함수가 정해진 뒤)
def get_metrics(funds: list[str], as_of: str) -> list[dict]:
    raise NotImplementedError("TODO: Metric 조회 (펀드·기준일로 거름)")


def get_verification(funds: list[str], as_of: str) -> list[dict]:
    raise NotImplementedError("TODO: ValidationResult 조회")


def judge_comparability(metric_ids: list[str], as_of: str) -> list[dict]:
    raise NotImplementedError("TODO: 정의 칸으로 코드가 판정, 각주만 LLM")


def search_rules(query: str, as_of: str | None = None) -> list[dict]:   # 실행기가 as_of를 늘 넣으므로 받기만 (규정은 기간과 무관)
    raise NotImplementedError("TODO: 규정 문서 검색")


def get_lineage(item_id: str, as_of: str) -> list[dict]:
    raise NotImplementedError("TODO: 계보 거슬러 원본 위치")


TOOLS = {name: globals()[s["fn"]] for name, s in TOOL_SCHEMAS.items()}   # 아직 안 씀 (지금은 fakes.TOOLS)
