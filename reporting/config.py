"""한도와 경로 설정. 숫자는 가안이며 실측 후 조정한다."""
import os
from pathlib import Path

# 실행 로그·최종 기록 저장 위치. 패키지 폴더 밖(실행한 위치의 logs/)에 두고, 환경 변수로 바꿀 수 있다.
LOG_DIR = Path(os.environ.get("REPORTING_LOG_DIR", "logs")).resolve()

MAX_JSON_ATTEMPTS = 3        # LLM 응답 형식 오류 재시도
PLAN_RETRY = 1               # 계획 검사에 걸렸을 때 재계획 횟수
MAX_REPLANS = 2              # 진행 판단에서 고르는 재계획 한도
MAX_TOOL_CALLS_PER_TASK = 6  # 실행기 툴 호출 한도 (작업 하나당)
REPORT_REWRITE = 1           # 보고서 검사에 걸렸을 때 재작성 횟수
MAX_TASKS = 5                # 하위 작업 최대 개수 (작업당 LLM 최대 8회 = 툴 루프 7 + 진행 판단 1, 보통 5회 안팎이라 예산 30 안에서 4~5개가 현실적)
MAX_TOTAL_LLM_CALLS = 30     # 실행 1건 전체 LLM 호출 예산. 보고서 작성은 예산에서 뺌 (예산이 바닥나도 모은 근거로 보고서는 써야 하므로)

# 노드 ID → 로그에 찍을 한글 이름 (흐름도 이름 그대로)
# LangGraph는 노드 이름과 State 키가 겹치면 안 돼서 ID는 plan → planner 식으로 둔다.
NODE_NAMES = {
    "start": "대화 시작",
    "understand": "양식·요청 이해",
    "planner": "작업 계획",
    "plan_checker": "계획 검사",
    "executor": "실행기",
    "progress_judge": "진행 판단",
    "replanner": "재계획",
    "composer": "보고서 작성",
    "report_checker": "보고서 검사",
    "human_review": "담당자 확인",
    "commit": "기록 확정",
}
