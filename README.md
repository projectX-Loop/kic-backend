# KIC 대체자산 사후관리 에이전트

PE 펀드 보고서 PDF에서 투자 지표와 원문 근거를 추출하고, Python으로 수치를 검증한 뒤 담당자가 검토할 보고서 초안을 작성하는 서비스다. 첫 검증 범위는 PDF 한 건에 공시된 펀드 하나의 특정 기준일 TVPI다.

## 서비스 흐름

1. FastAPI가 PDF를 접수하고 처리 상태를 제공한다. 처리 과정에서 원본 파일 참조와 해시를 문서 판본에 연결한다.
2. Python PDF 라이브러리와 PaddleOCR로 표·본문·머리글을 읽어 관측값과 검색용 청크를 만든다. 원문 표기와 통화·배율의 근거를 같은 판본의 페이지·셀·머리글에 연결한다.
3. 검증 코드가 공시값과 별도로 TVPI를 재계산한다. 펀드·기준일·산출 기준이 일치하고 통화·배율이 정렬되면 `(누적 분배액 + 잔존가치) / 누적 납입액`을 계산한다. 필수 값 누락, 납입액 0, 기준 불일치, 판정 정책 미확정은 해당 단계의 보류 사유로 기록한다.
4. PostgreSQL에 공시 관측값, 재계산값, 판정 상태와 사유, 실제 계산 입력의 관측값 ID·역할을 저장한다. 각 입력에서 같은 판본의 원문 근거까지 추적할 수 있게 한다.
5. LangGraph는 검증 결과와 원문 근거를 조회해 초안을 작성한다. 검토 화면에 보고서 문단, 인용한 검증 결과·근거 ID, 담당자가 확인할 항목을 반환한다.

## 파일 구조와 담당

아래 표는 구현할 파일·디렉토리의 위치를 포함한다. 현재 FastAPI 진입점, 프로세스 상태를 확인하는 `GET /health`, OpenAPI 문서를 실행할 수 있다.

| 경로 | 담당 | 역할 |
| --- | --- | --- |
| `app/__init__.py` | 권도윤 | `app`을 명시적인 Python 패키지로 선언 |
| `app/main.py` | 권도윤 | FastAPI 앱 생성 및 라우터 등록 |
| `app/routers/` | 권도윤 | 프로세스 상태 확인, PDF 접수 및 처리 상태 조회의 HTTP 요청·응답 |
| `app/orchestrator.py`, `app/services/` | 권도윤 | 전체 흐름은 `orchestrator.py`에서 연결하고, 개별 서비스 로직은 `services/`에 배치 |
| `app/repositories/` | 권도윤 | 서비스가 사용하는 도메인 단위 저장·조회. 트랜잭션을 묶고 쿼리 결과를 도메인 객체로 변환 |
| `extraction/` | 이승준 | PDF를 읽어 원문 근거가 붙은 관측값과 검색용 청크 반환 |
| `validation/` | 권도윤 | 관측값을 `Decimal`로 처리하고 재계산값·판정·사유 반환. 계산 함수는 DB 없이 테스트 가능하게 유지 |
| `reporting/` | 성종현 | LangGraph에서 조회할 근거, 작성할 문단, 확인이 필요한 항목을 결정 |
| `db/` | 권도윤 | PostgreSQL 연결과 저수준 SQL. ID로 원문·검증 결과를 읽는 SQL 및 스키마·검색 인덱스 마이그레이션 관리 |
| `retrieval/` | 권도윤 | 질의에 맞는 근거를 찾고 순위를 매기는 SQL. BM25·벡터 검색과 RRF 순위 결합 |
| `tests/`, `samples/`, `docs/` | 공통 | 자동화 테스트, 인계 예시 데이터, 설계 문서 |
| `pyproject.toml` | 공통 | Python 버전, 의존성, 배포 패키지 설정 |

`app/repositories/`는 도메인 단위의 저장·조회를 제공하고, 이를 수행할 때 `db/`의 연결과 SQL을 사용한다. `retrieval/`도 `db/`의 연결을 사용하며 의존 방향은 단방향이다. 검색 인덱스 DDL과 한국어 형태소 분석용 `pg_search` Lindera 설정은 `db/`의 마이그레이션에 두고, 향후 검색 실험은 `retrieval/`에서 진행한다. [ParadeDB 토크나이저 안내](https://www.paradedb.com/blog/v2api)

DB 접근은 Psycopg와 SQL을 기본으로 한다. 첫 저장·조회 기능을 구현할 때 `db/`에 연결 관리를 두고, 함께 저장해야 하는 관측값·검증 결과·계산 입력의 트랜잭션 범위는 `app/repositories/`에서 정한다. 테이블과 인덱스는 SQL 마이그레이션으로 관리하므로 현재 설계에서 ORM 모델과 ORM 세션은 선택 사항이다.

`app/orchestrator.py`는 대상 문서 판본을 고정한 검색 함수를 `reporting/`에 전달한다. 보고서 작성 흐름은 검색어를 정하고, `retrieval/`은 해당 판본으로 후보 범위를 제한한 뒤 순위를 매긴다.

## 로컬 실행

Python 3.13과 uv 0.13.10 이상을 사용한다. Python 3.13은 PaddlePaddle의 macOS ARM64 휠이 제공되는 최신 안정 계열이다. FastAPI 0.165.0과 Uvicorn 0.54.0을 포함한 설치 버전은 `uv.lock`으로 관리한다. [Python 지원 일정](https://www.python.org/downloads/) · [PaddlePaddle 배포 파일](https://pypi.org/project/paddlepaddle/#files)

uv 설치 후 저장소 루트에서 실행한다. `uv sync`는 `pyproject.toml`의 `requires-python` 범위에 맞는 Python을 선택해 프로젝트의 `.venv/`를 준비한다. [uv 프로젝트 설정](https://docs.astral.sh/uv/concepts/projects/config/#python-version-requirement)

```sh
uv sync --locked
uv run --locked uvicorn app.main:app
```

`uv sync`는 이 저장소를 하나의 `kic-backend` 배포 패키지로 `.venv/`에 편집 가능 모드로 설치한다. `app`, `db`, `extraction`, `reporting`, `retrieval`, `validation`은 각각의 `__init__.py`를 통해 Python 패키지로 등록되며, 저장소 밖에서도 이 가상환경의 Python으로 가져올 수 있다.

서버가 실행되면 별도 터미널에서 상태를 확인하거나 API 문서를 연다. [API 문서](http://127.0.0.1:8000/docs)

```sh
curl -fsS http://127.0.0.1:8000/health
```

도메인 개발에 필요한 의존성은 `uv sync --locked --extra extraction`, `--extra postgres`, `--extra reporting`으로 선택한다. `extraction`은 PaddleOCR·PaddlePaddle·pypdf, `postgres`는 Psycopg, `reporting`은 LangGraph를 설치한다. 같은 환경에서 모두 사용하려면 `uv sync --locked --all-extras`를 실행한다. 배포용 wheel은 `uv build --wheel`로 빌드한다.

## 테스트와 샘플

`tests/test_health.py`는 `/health`의 HTTP 200 응답과 본문을 확인한다.

```sh
uv run --locked pytest tests/test_health.py
uv run --locked pytest
```

[`samples/synthetic_tvpi.json`](samples/synthetic_tvpi.json)은 TVPI 검증에 사용할 합성 입력과 기대값이다. 금융 수치는 `Decimal`로 읽을 수 있도록 문자열로 저장했다.
