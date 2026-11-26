"""구현된 그래프를 mermaid 그림으로 뽑는다.

  python -m reporting.draw_graph                      →  reporting/graph.mmd 생성 (코드가 바뀌면 다시 실행)
  python -m reporting.draw_graph --readme README.md   →  그 파일의 <!-- graph:start -->~<!-- graph:end --> 구간도 갱신

노드·화살표는 컴파일된 그래프에서 그대로 읽고, 한글 이름·갈림길 라벨·역할 색만 붙인다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import NODE_NAMES
from .graph import build_graph

# 갈림길 라벨 (graph.py의 after_* 함수 조건과 같은 내용)
EDGE_LABELS = {
    ("understand", "planner"): "정상",
    ("understand", "commit"): "작성 보류 · LLM 실패",
    ("planner", "plan_checker"): "정상",
    ("planner", "commit"): "LLM 실패 → 실패 기록",
    ("executor", "progress_judge"): "정상",
    ("replanner", "plan_checker"): "정상",
    ("executor", "composer"): "실패 → 미완 표시",
    ("replanner", "composer"): "실패 → 미완 표시",
    ("plan_checker", "executor"): "통과",
    ("plan_checker", "planner"): "첫 계획 위반 · 1회 다시",
    ("plan_checker", "composer"): "재계획 위반 → 미완 표시",
    ("plan_checker", "commit"): "다시 짜도 위반 → 작성 보류",
    ("progress_judge", "executor"): "계속",
    ("progress_judge", "replanner"): "재계획 (최대 2회)",
    ("progress_judge", "composer"): "종료 · 한도(미완 표시)",
    ("report_checker", "composer"): "위반 · 1회 재작성",
    ("report_checker", "human_review"): "통과 → 항상 확인",
    ("human_review", "human_review"): "잘못된 응답",
    ("human_review", "commit"): "승인·반려",
}

# 누가 하는 노드인지 (LLM / 코드 / 사람)
ROLE = {
    "llm": ["understand", "planner", "executor", "progress_judge", "replanner", "composer"],
    "code": ["start", "plan_checker", "report_checker", "commit"],
    "human": ["human_review"],
}
EXTRA = {"executor": "툴 루프 (작업당 최대 6회)", "human_review": "interrupt ⏸", "composer": "이름표 → 코드가 값 채움"}


def to_mermaid() -> str:
    g = build_graph().get_graph()
    lines = ['%%{init: {"flowchart": {"curve": "basis", "nodeSpacing": 50, "rankSpacing": 50}}}%%',
             "flowchart TD"]
    for nid in g.nodes:
        if nid in ("__start__", "__end__"):
            lines.append(f"    {nid}((\"{'시작' if nid == '__start__' else '끝'}\"))")
            continue
        label = f"<b>{NODE_NAMES[nid]}</b>" + (f"<br/>{EXTRA[nid]}" if nid in EXTRA else "")
        shape = '(["{}"])' if nid == "human_review" else '("{}")'   # 사람만 알약 모양, 나머지는 둥근 상자
        lines.append(f"    {nid}{shape.format(label)}")
    order = ["__start__", *NODE_NAMES, "__end__"]               # 흐름 순서대로 그려야 배치가 깔끔함
    for e in sorted(g.edges, key=lambda e: (order.index(e.source), order.index(e.target))):
        arrow = "-.->" if e.conditional else "==>"            # 굵은 선 = 고정 흐름, 점선 = 갈림길
        lab = EDGE_LABELS.get((e.source, e.target))
        lines.append(f"    {e.source} {arrow}" + (f'|"{lab}"| ' if lab else " ") + e.target)
    lines += [
        "    classDef llm fill:#e3edff,stroke:#3b6fd4,stroke-width:1.5px,color:#14213d",
        "    classDef code fill:#e6f4ea,stroke:#3c8a4f,stroke-width:1.5px,color:#14213d",
        "    classDef human fill:#fff1dc,stroke:#d0821c,stroke-width:2px,color:#14213d",
        "    classDef term fill:#f1f1f1,stroke:#8a8a8a,color:#333",
    ]
    for role, ids in ROLE.items():
        lines.append(f"    class {','.join(ids)} {role}")
    lines.append("    class __start__,__end__ term")
    return "\n".join(lines) + "\n"


START, END = "<!-- graph:start -->", "<!-- graph:end -->"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--readme", type=Path, help="그래프 표시 구간을 갱신할 마크다운 파일 (GitHub가 mermaid를 직접 그려 줌)")
    a = p.parse_args()
    mmd = to_mermaid()
    out = Path(__file__).with_name("graph.mmd")
    out.write_text(mmd, encoding="utf-8")
    print(f"저장: {out}")
    if a.readme and a.readme.exists() and START in (text := a.readme.read_text(encoding="utf-8")):
        head, rest = text.split(START, 1)
        tail = rest.split(END, 1)[1]
        a.readme.write_text(f"{head}{START}\n```mermaid\n{mmd}```\n{END}{tail}", encoding="utf-8")
        print(f"갱신: {a.readme} 그래프")
