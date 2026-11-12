"""放行/拒绝决定的可审计存取：对哪次编排的哪个作业、谁、何时、决定是什么。

每个 (orchestration_id, job_id) 只允许一条决定（数据库唯一约束），
决定不可改不可删 —— 要改主意只能开新一趟编排，保证审计轨迹干净。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlite3


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def insert_decision(
    conn: sqlite3.Connection,
    *,
    orchestration_id: int,
    job_id: int,
    decision: str,
    actor: str,
    note: str = "",
) -> int:
    if decision not in ("released", "rejected"):
        raise ValueError(f"invalid decision: {decision}")
    cur = conn.execute(
        """
        INSERT INTO release_decisions(orchestration_id, job_id, decision, actor, note, decided_at)
        VALUES (?,?,?,?,?,?)
        """,
        (orchestration_id, job_id, decision, actor, note, _now()),
    )
    return int(cur.lastrowid)


def get_decision(
    conn: sqlite3.Connection, orchestration_id: int, job_id: int
) -> dict[str, Any] | None:
    r = conn.execute(
        """
        SELECT id, orchestration_id, job_id, decision, actor, note, decided_at
        FROM release_decisions WHERE orchestration_id=? AND job_id=?
        """,
        (orchestration_id, job_id),
    ).fetchone()
    return dict(r) if r else None


def list_decisions(
    conn: sqlite3.Connection, orchestration_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.id, d.orchestration_id, d.job_id, j.name AS job_name,
               d.decision, d.actor, d.note, d.decided_at
        FROM release_decisions d JOIN jobs j ON j.id=d.job_id
        WHERE d.orchestration_id=?
        ORDER BY d.id
        """,
        (orchestration_id,),
    ).fetchall()
    return [dict(r) for r in rows]
