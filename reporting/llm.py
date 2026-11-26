"""LLM 호출 한 곳. 모든 노드가 이 함수로 LLM을 부른다.

형식이 틀리면 MAX_JSON_ATTEMPTS번까지 다시 부르고, 시도마다 llm_call 로그를 남긴다.
지금은 fakes.fake_llm을 부른다. 실제 모델로 바꿀 때는 _raw_call 안쪽만 바꾼다.
LLM에 보낼 데이터는 context.py가 노드별로 고르고, 지시문은 prompts.py에 있다. 둘을 합친 메시지를 _raw_call이 보낸다.
(지금은 가짜 LLM이라 메시지는 만들기만 하고 크기를 로그에 남긴다.)
"""
from __future__ import annotations

import json
import time
from typing import Any

from .config import MAX_JSON_ATTEMPTS, MAX_TOTAL_LLM_CALLS
from .context import BUILDERS, build_context
from .fakes import BadJSON, fake_llm
from .prompts import PROMPTS, build_messages
from .runlog import get_logger

assert set(PROMPTS) == set(BUILDERS), "지시문(prompts.py)과 데이터(context.py)의 purpose가 다름"


class LLMFailed(Exception):
    """재시도를 다 써도 형식이 맞는 응답을 못 받음. attempts = 실제로 쓴 호출 수."""
    def __init__(self, msg: str, attempts: int):
        super().__init__(msg)
        self.attempts = attempts


class LLMBudgetExceeded(LLMFailed):
    """실행 1건의 전체 LLM 호출 예산을 다 씀 (한도 종료)."""


def _raw_call(purpose: str, scenario: str, state: dict, messages: list[dict], attempt: int) -> dict:
    # TODO(모델 확정 후): messages를 모델에 보내고 JSON 파싱. state는 그때 안 쓴다.
    #   resp = client.chat.completions.create(model=..., messages=messages, response_format={"type": "json_object"})
    #   return json.loads(resp.choices[0].message.content)
    # 지금 가짜 LLM은 시나리오 흉내에 State가 필요해서 state를 받는다.
    return fake_llm(purpose, scenario, state, attempt)


def _size(x: Any) -> int:
    return len(json.dumps(x, ensure_ascii=False, default=str))


def call_json(node: str, purpose: str, state: dict, config: dict,
              required: tuple[str, ...] = (), budgeted: bool = True) -> tuple[dict[str, Any], int]:
    """(응답, 시도 횟수)를 돌려준다. required 키가 없으면 형식 오류로 본다.

    실패하면 LLMFailed(형식 오류 소진) 또는 LLMBudgetExceeded(전체 예산 소진)를 던진다.
    노드가 이걸 잡아 실패 경로로 보낸다 (실행 전 → 실패 기록, 실행 중 → 모은 근거로 작성·미완).
    """
    log = get_logger(state["run_id"])
    scenario = config["configurable"].get("scenario", "normal")
    budget = config["configurable"].get("max_llm_calls", MAX_TOTAL_LLM_CALLS)
    used = state.get("counters", {}).get("llm_calls", 0)
    context = build_context(purpose, state) # 노드별로 필요한 것만 (context.py)
    messages = build_messages(purpose, context) # 지시문(prompts.py) + 데이터
    sent = {"sent_keys": sorted(context), "sent_chars": _size(context), "state_chars": _size(state),
            "prompt_chars": len(messages[0]["content"])}
    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        if budgeted and used + attempt - 1 >= budget:
            log.log(node, "limit_hit", "fail", f"전체 LLM 호출 예산 {budget}회 소진")
            raise LLMBudgetExceeded(f"전체 LLM 호출 예산 {budget}회 소진", attempt - 1)
        t0 = time.perf_counter()
        try:
            data = _raw_call(purpose, scenario, state, messages, attempt)
            missing = [k for k in required if k not in data]
            if missing:
                raise BadJSON(f"필수 키 없음 {missing}")
        except BadJSON as e:
            ms = int((time.perf_counter() - t0) * 1000)
            log.log(node, "llm_call", "retry", f"{purpose} 시도 {attempt} 형식 오류: {e}",
                    {"purpose": purpose, "attempt": attempt}, ms)
            continue
        ms = int((time.perf_counter() - t0) * 1000)
        log.log(node, "llm_call", "ok", f"{purpose} 시도 {attempt} 성공",
                {"purpose": purpose, "attempt": attempt, "keys": sorted(data), **sent}, ms)
        return data, attempt
    log.log(node, "limit_hit", "fail", f"{purpose} 형식 오류 {MAX_JSON_ATTEMPTS}회")
    raise LLMFailed(f"{purpose} 응답 형식 오류 {MAX_JSON_ATTEMPTS}회", MAX_JSON_ATTEMPTS)
