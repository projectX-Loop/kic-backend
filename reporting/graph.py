"""그래프 조립 (큰 틀은 흐름도, 세부 규칙은 기획서 6쪽). 분기는 모두 코드가 정하고 route 로그를 남긴다.

LLM 노드가 실패하면(형식 오류 소진·전체 예산 소진·툴 막힘) aborted=True:
  실행 전(양식·요청 이해, 작업 계획) → 기록 확정(실패 기록) / 실행 중(실행기·진행 판단·재계획) → 보고서 작성(미완 표시)
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .config import NODE_NAMES
from .nodes import NODES
from .runlog import get_logger
from .state import AgentState


def _go(state: dict, src: str, dest: str, why: str = "") -> str:
    get_logger(state["run_id"]).log(src, "route", summary=f"→ {NODE_NAMES[dest]}" + (f" ({why})" if why else ""),
                                    data={"to": dest, "reason": why})
    return dest


def after_understand(state: dict) -> str:
    if state.get("aborted"):
        return _go(state, "understand", "commit", "LLM 실패 → 실패 기록")
    if state.get("notice"):
        return _go(state, "understand", "commit", "범위 밖·양식 정보 부족 → 작성 보류")
    return _go(state, "understand", "planner")


def after_planner(state: dict) -> str:
    if state.get("aborted"):
        return _go(state, "planner", "commit", "LLM 실패 → 실패 기록")
    return _go(state, "planner", "plan_checker")


def after_executor(state: dict) -> str:
    if state.get("aborted"):
        return _go(state, "executor", "composer", "실패 → 모은 근거로 작성·미완 표시")
    return _go(state, "executor", "progress_judge")


def after_replanner(state: dict) -> str:
    if state.get("aborted"):
        return _go(state, "replanner", "composer", "실패 → 모은 근거로 작성·미완 표시")
    return _go(state, "replanner", "plan_checker")


def after_plan_check(state: dict) -> str:
    nxt = state["plan_check"]["next"]
    why = {"planner": "첫 계획 위반 → 1회 다시",
           "composer": "재계획 위반 → 모은 근거로 작성·미완 표시",
           "commit": "다시 짜도 위반 → 작성 보류"}.get(nxt, "통과")
    return _go(state, "plan_checker", nxt, why)


def after_progress(state: dict) -> str:
    dest = {"continue": "executor", "replan": "replanner", "finish": "composer"}[state["progress"]["decision"]]
    return _go(state, "progress_judge", dest, state["progress"]["reason"])


def after_report_check(state: dict) -> str:
    nxt = state["check"]["next"]
    why = {"composer": "위반 → 1회 재작성", "human_review": "초안 완성"}[nxt]
    return _go(state, "report_checker", nxt, why)


def after_review(state: dict) -> str:
    if state.get("review_error"):
        return _go(state, "human_review", "human_review", "잘못된 응답")
    return _go(state, "human_review", "commit", state["review"]["action"])


def build_graph(checkpointer=None):
    g = StateGraph(AgentState) # state.py 사용
    for name, fn in NODES.items(): # nodes.py 노드 11개
        g.add_node(name, fn) # 노드 등록

    # 경로 설정
    g.add_edge(START, "start")
    g.add_edge("start", "understand")
    g.add_conditional_edges("understand", after_understand, ["planner", "commit"]) # 실패면 commit, 보류면 commit, 아니면 planner
    g.add_conditional_edges("planner", after_planner, ["plan_checker", "commit"]) # 실패면 commit, 아니면 plan_checker
    g.add_conditional_edges("plan_checker", after_plan_check, ["executor", "planner", "composer", "commit"]) # executor(통과) / planner(첫 계획 위반) / composer(재계획 위반) / commit(다시 짜도 위반)
    g.add_conditional_edges("executor", after_executor, ["progress_judge", "composer"]) # 실패면 composer, 아니면 progress_judge
    g.add_conditional_edges("progress_judge", after_progress, ["executor", "replanner", "composer"]) # continue → executor / replan → replanner / finish → composer
    g.add_conditional_edges("replanner", after_replanner, ["plan_checker", "composer"]) # 실패면 composer, 아니면 plan_checker
    g.add_edge("composer", "report_checker")
    g.add_conditional_edges("report_checker", after_report_check, ["composer", "human_review"]) # composer(재작성) / human_review
    g.add_conditional_edges("human_review", after_review, ["human_review", "commit"]) # 응답 형식이 틀리면 human_review(다시 물음), 아니면 commit
    g.add_edge("commit", END)
    # 지금은 메모리 체크포인터. 인프라 나오면 PostgreSQL 체크포인터로 교체.
    return g.compile(checkpointer=checkpointer or InMemorySaver())
