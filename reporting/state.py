"""에이전트 State (틀 수준). 세부 필드는 구현하면서 채운다."""
from __future__ import annotations

from typing import Any, TypedDict


class Task(TypedDict): # 작업 하나 단위
    id: int
    goal: str # 자유 문장 작업 설명
    sections: list[str] # 이 작업이 채울 양식 절
    targets: list[str] # 대상 펀드
    period: str # 대상 기간 (요청 기준일과 같아야 함)
    depends_on: list[int] # 의존성
    done_criteria: list[str] # 필요한 근거 종류 (예: metric, verification, judgment)
    status: str # pending | in_progress | completed | failed | replaced
    # limited: str  # (있을 때만) 툴 한도·에러로 근거 일부만 모은 사유 → [미완] 표시


class AgentState(TypedDict, total=False): # 실행 하나 전체 단위
    # 실행
    run_id: str # 실행 ID
    status: str # 현재 상태 running | waiting_review | completed | partial | rejected | held(작성 보류) | failed(실행 전 실패)
    request: dict[str, Any] # 사용자 요청 template_id, request_text, funds, as_of
    template: dict[str, Any] # 양식 정의

    # 해석·계획
    spec: dict[str, Any] # 사용자의 말을 에이전트가 쓸 수 있는 형태로 바꾼 결과
    notice: str | None # 작성 보류 사유 (범위 밖·양식 정보 부족·실행 전 계획 실패) → 보고서 없이 안내만
    stop_reason: str | None # 실행 중 멈춘 사유 (한도·재계획 실패) → 모은 근거로 작성 + 미완 표시
    plan_source: str | None # planner | replanner (계획 검사가 어느 쪽 계획인지 구분)
    aborted: bool # LLM 실패·예산 소진 등으로 실패 경로를 탐
    plan: list[Task] # 작업(Task) 목록
    plan_check: dict[str, Any] | None # ok, violations, next (계획 검사 결과)
    plan_retry: int # 한도와 비교하는 횟수
    replan_count: int # 한도와 비교하는 횟수
    current_task: int | None # plan 목록에서 지금 실행 중인 작업 위치
    
    # 실행 결과
    evidence: dict[str, dict[str, Any]] # 참조 ID → 툴 결과 원본 + 출처
    progress: dict[str, str] | None # 진행 판단 결과 : continue | replan | finish, reason(결정 이유)

    # 작성·검사·검토
    draft: dict[str, Any] | None # 승인 대기 중인 보고서(현재 시점의 최신 보고서)
    rewrite_count: int # 재작성 횟수
    check: dict[str, Any] | None # ok, violations, next (보고서 검사 결과)
    review_queue: list[dict[str, Any]] # 담당자에게 보여줄 검토 대기 항목
    review: dict[str, Any] | None # 담당자 응답
    review_error: str | None # 담당자 응답 형식 틀렸을 때 사유
    counters: dict[str, int] # llm_calls, tool_calls 횟수
