"""실행 로그. JSON Lines로 파일에 남기고 화면에는 한 줄 요약을 찍는다.

출력처는 나중에 DB로 바꿀 수 있게 RunLogger 한 곳에 모아 둔다.
"""
from __future__ import annotations

import functools
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp

from .config import LOG_DIR, NODE_NAMES

_LOGGERS: dict[str, "RunLogger"] = {}


class RunLogger:
    def __init__(self, run_id: str, echo: bool = True):
        self.run_id = run_id
        self.seq = 0
        self.echo = echo
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"{run_id}.jsonl"

    def log(self, node: str, event: str, status: str = "ok", summary: str = "",
            data: dict[str, Any] | None = None, duration_ms: int | None = None) -> None:
        self.seq += 1
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "seq": self.seq,
            "node": NODE_NAMES.get(node, node),
            "event": event,
            "status": status,
            "summary": summary,
            "data": data or {},
            "duration_ms": duration_ms,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.echo:
            mark = {"ok": " ", "retry": "↻", "fail": "✗", "wait": "⏸"}.get(status, " ")
            print(f"{self.seq:>3} {mark} {record['node']:<8} {event:<10} {summary}")


def get_logger(run_id: str) -> RunLogger:
    if run_id not in _LOGGERS:
        _LOGGERS[run_id] = RunLogger(run_id)
    return _LOGGERS[run_id]


def logged_node(node_id: str) -> Callable:
    """노드 진입·종료와 소요 시간, 바뀐 State 키를 남기는 데코레이터."""
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(state: dict, config: RunnableConfig) -> dict:
            log = get_logger(state["run_id"])
            log.log(node_id, "node_start")
            t0 = time.perf_counter()
            try:
                update = fn(state, config)
            except GraphBubbleUp:  # interrupt(): 오류가 아니라 담당자 응답 대기
                log.log(node_id, "interrupt", "wait", "담당자 응답 대기 (그래프 일시정지)")
                raise
            except Exception as e:  # 치명적 오류: 로그 남기고 다시 던진다 (체크포인트에서 재시도)
                log.log(node_id, "error", "fail", f"{type(e).__name__}: {e}")
                raise
            ms = int((time.perf_counter() - t0) * 1000)
            log.log(node_id, "node_end", data={"updated": sorted(update or {})}, duration_ms=ms)
            return update
        return inner
    return wrap
