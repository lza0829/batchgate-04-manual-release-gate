"""Manual release gate: jobs flagged requires_release need a per-orchestration human decision.

闸态机（对某一趟编排的某个作业）：
- open：未标 requires_release，不设闸；
- pending：已设闸但当趟还没有放行/拒绝决定 —— 不得跑夹具，记 skipped_release；
- released：当趟已放行 —— 可以执行；
- rejected：当趟已拒绝 —— 本趟不得执行，记 skipped_release，下游按依赖规则跳过。

决定只来自人工操作（release_decisions 表，按 编排×作业 唯一），
不读取上游行数，不做任何自动评分/自动抬水位。
"""

from __future__ import annotations

from typing import Any

OPEN = "open"
PENDING = "pending"
RELEASED = "released"
REJECTED = "rejected"


def gate_state(requires_release: bool, decision: dict[str, Any] | None) -> str:
    if not requires_release:
        return OPEN
    if decision is None:
        return PENDING
    return RELEASED if decision["decision"] == "released" else REJECTED
