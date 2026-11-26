# 저장소 작업 규칙

## 수치와 근거

- 금융 수치는 Python `Decimal`과 PostgreSQL `NUMERIC`으로 다룬다. 원문 표기, 계산용 값, 통화, 배율을 구분하고 누락값을 0으로 채우지 않는다.
- 공시 관측값과 코드 재계산값을 각각 보존한다. 검증 결과마다 실제 입력 관측값 ID와 역할을 기록하고, 각 관측값에서 동일 문서 판본의 원문 위치까지 추적할 수 있게 한다.
- TVPI는 펀드·기준일·산출 기준이 일치하고 금액의 통화·배율이 정렬된 경우에만 (누적 분배액 + 잔존가치) / 누적 납입액으로 계산한다. 납입액이 0이거나 필수 값이 누락되거나 기준이 맞지 않으면 계산을 보류하고 사유를 반환한다.
- 허용오차 또는 반올림 정책이 미정이면 자동 통과 판정을 내리지 않는다.
- 계산 규칙은 PDF 파서·DB 연결·LLM 호출 없이 단위 테스트할 수 있게 작성한다. 합성 입력·기대값에는 합성임을 표시해 실제 PDF 추출 결과와 구분한다.
- OCR 출력 형식, 좌표 체계, 이미지 생성 방식은 실제 샘플 확인 후 정한다. 기존 리밸런싱 서비스 API와 `knowledge_*` 스키마를 KIC 서비스 계약으로 간주하지 않는다.

## 구현과 검토

- Python 표준 명명·들여쓰기와 프레임워크의 일반적인 구조를 따른다. 첫 구현 시 이 문서에 진입점·테스트·사용 자산의 실제 경로를 기록한다.
- 도구 도입 시 필수 버전, 설치·시작·빌드·테스트 명령을 새 체크아웃에서 검증해 문서화한다. 필요한 환경 변수에는 안전한 예시 값을 제공한다.
- 첫 테스트를 추가할 때 예제 테스트 파일명과 개별·전체 실행 명령을 기록한다. 포매터·린터 도입 시 설정과 실행 명령을 함께 커밋하고, 커버리지 기준은 이를 강제하는 설정과 함께 문서화한다.
- 구현 전에 관련 명세와 기존 코드를 확인한다. 결과나 데이터 계약에 영향을 주는 미정 사항은 명시하고, 실제 표본이 필요한 결정은 표본 확인까지 보류한다.
- 요청한 동작을 검증할 수 있는 범위로 변경을 제한한다. 관련 없는 리팩터링과 새 의존성 도입은 별도 변경으로 다룬다.

## 진입점

- FastAPI 진입점은 `app/main.py`의 `app`이다. Python 버전과 설치·실행·확인 명령은 [README의 로컬 실행](README.md#로컬-실행)에 정리한다.
- 기본 의존성은 `uv sync --locked`로 설치하고, 배포용 wheel은 `uv build --wheel`로 빌드한다.
- 예제 테스트는 `tests/test_health.py`다. 개별 실행은 `uv run --locked pytest tests/test_health.py`, 전체 실행은 `uv run --locked pytest`를 사용한다.
- 합성 TVPI 입력과 기대값 예시는 `samples/synthetic_tvpi.json`에 둔다.
- 보고서 작성 에이전트는 `reporting/`이다. LangGraph는 `uv sync --locked --extra reporting`으로 설치한다. 합성 데이터·가짜 LLM 시나리오는 `uv run --locked --extra reporting python -m reporting.run --all`로 실행하고, 로그는 실행 위치의 `logs/`(`REPORTING_LOG_DIR`로 변경)에 남는다.
- 보고서 시나리오 테스트는 `tests/test_reporting_scenarios.py`다. 개별 실행은 `uv run --locked --extra reporting pytest tests/test_reporting_scenarios.py`를 사용한다. `reporting` extra가 없으면 건너뛴다.

## 커밋과 PR

- 커밋 생성에는 [smart-commit](.agents/skills/smart-commit/SKILL.md)을, PR 생성에는 [smart-pr](.agents/skills/smart-pr/SKILL.md)을 사용한다. 원격 푸시는 사용자가 직접 수행한다.

- 커밋 하나에는 한 가지 의도를 담는다. 동작 변경과 그 동작을 검증하는 테스트는 함께 묶는다.
- 커밋과 PR 제목은 `Type(Scope) : Description` 형식을 쓴다. 유형은 기능 `Feat`, 수정 `Fix`, 구조 개선 `Refactor`, 테스트 `Test`, 문서 `Docs`, 도구·설정 `Chore`에서 고른다. 범위는 `validation`, `db`, `repo`처럼 대표 영역 하나로 적는다.
- 설명은 대문자로 시작하는 짧은 영어 명령형으로 쓰고 끝에 마침표를 붙이지 않는다. 예시는 `Fix(validation) : Defer TVPI calculation for zero contributions`이다.
- PR 제목은 전체 변경의 목적을 요약한다. 본문은 한국어로 변경 이유와 내용, 관련 이슈가 있으면 링크, 수행한 검사와 결과를 적는다. 실행하지 못한 검사는 사유를 밝힌다. API 변경에는 예시 요청과 예상 응답을 포함한다.
