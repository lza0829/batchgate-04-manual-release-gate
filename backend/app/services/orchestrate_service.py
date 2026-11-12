from __future__ import annotations

import sqlite3
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.dependency import topological_layers
from app.engines.job_runner import run_fixture_job
from app.modules import release_gate
from app.repositories import jobs as jobs_repo
from app.repositories import orchestrations as orch_repo
from app.repositories import releases as releases_repo
from app.repositories import watermarks as wm_repo

FINAL_STATUSES = ("success", "failed")


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "job_count": conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"],
            "enabled_jobs": conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE enabled=1"
            ).fetchone()["c"],
            "orchestration_count": conn.execute(
                "SELECT COUNT(*) c FROM orchestrations"
            ).fetchone()["c"],
            "awaiting_release": conn.execute(
                "SELECT COUNT(*) c FROM orchestrations WHERE status='awaiting_release'"
            ).fetchone()["c"],
            "attempt_count": conn.execute(
                "SELECT COUNT(*) c FROM job_attempts"
            ).fetchone()["c"],
            "watermarked_jobs": conn.execute(
                "SELECT COUNT(*) c FROM watermarks WHERE watermark_ts IS NOT NULL"
            ).fetchone()["c"],
        }
    finally:
        conn.close()


def _latest_attempts(
    conn: sqlite3.Connection, orch_id: int
) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    """本趟每个作业的最近一次记录（按 id 最大者）与已用 attempt_no。"""
    latest: dict[int, dict[str, Any]] = {}
    attempt_nos: dict[int, int] = {}
    for a in orch_repo.list_attempts(conn, orch_id):
        latest[a["job_id"]] = a
        attempt_nos[a["job_id"]] = max(attempt_nos.get(a["job_id"], 0), a["attempt_no"])
    return latest, attempt_nos


def _execute_pass(conn: sqlite3.Connection, orch_id: int) -> None:
    """按依赖层走一遍图。

    每个作业只会被推进一次状态机：
    - success/failed 为终态，不再重评；
    - skipped_dep / skipped_release 不是终态，下一趟（resume）若前提变化会被重评，
      重评结果不变则不重复写记录（去重），变了则追加一条 attempt_no+1 的记录。
    """
    jobs = [j for j in jobs_repo.list_jobs(conn) if j["enabled"]]
    decisions = {
        d["job_id"]: d for d in releases_repo.list_decisions(conn, orch_id)
    }
    latest_by_id, attempt_nos = _latest_attempts(conn, orch_id)
    status_by_name: dict[str, str] = {}
    for j in jobs_repo.list_jobs(conn):
        rec = latest_by_id.get(j["id"])
        if rec:
            status_by_name[j["name"]] = rec["status"]

    def record(job: dict[str, Any], status: str, detail: dict[str, Any]) -> None:
        cur = latest_by_id.get(job["id"])
        if cur and cur["status"] == status and cur["detail"] == detail:
            return  # 评估结果没变，不重复记录
        no = attempt_nos.get(job["id"], 0) + 1
        orch_repo.insert_attempt(
            conn,
            orchestration_id=orch_id,
            job_id=job["id"],
            attempt_no=no,
            status=status,
            detail=detail,
        )
        attempt_nos[job["id"]] = no
        latest_by_id[job["id"]] = {"status": status, "detail": detail}
        status_by_name[job["name"]] = status

    for layer in topological_layers(jobs):
        for job in layer:
            cur = latest_by_id.get(job["id"])
            if cur and cur["status"] in FINAL_STATUSES:
                continue

            upstream_bad = {
                name: status_by_name.get(name, "no_record")
                for name in job["depends_on"]
                if status_by_name.get(name) != "success"
            }
            if upstream_bad:
                record(
                    job,
                    "skipped_dep",
                    {
                        "reason": "upstream not success: " + ", ".join(upstream_bad),
                        "upstream_status": upstream_bad,
                    },
                )
                continue

            decision = decisions.get(job["id"])
            state = release_gate.gate_state(job["requires_release"], decision)
            if state == release_gate.PENDING:
                record(
                    job,
                    "skipped_release",
                    {
                        "reason": "requires manual release; no decision for this orchestration",
                        "gate": "pending",
                    },
                )
                continue
            if state == release_gate.REJECTED:
                record(
                    job,
                    "skipped_release",
                    {
                        "reason": f"rejected by {decision['actor']} at {decision['decided_at']}",
                        "gate": "rejected",
                        "decision_id": decision["id"],
                        "note": decision["note"],
                    },
                )
                continue

            try:
                result = run_fixture_job(FIXTURES_DIR / job["fixture_path"])
                if state == release_gate.RELEASED:
                    result["gate"] = "released"
                    result["decision_id"] = decision["id"]
                    result["released_by"] = decision["actor"]
                record(job, "success", result)
                wm_repo.set_watermark(
                    conn, job["id"], result["watermark_ts"], result["artifact_rows"]
                )
            except Exception as exc:  # noqa: BLE001
                record(job, "failed", {"error": str(exc)})


def _build_report(conn: sqlite3.Connection, orch_id: int) -> dict[str, Any]:
    """报告只统计每个作业本趟的最近一次记录，保证与尝试记录、放行记录对得上。"""
    jobs = [j for j in jobs_repo.list_jobs(conn) if j["enabled"]]
    decisions = releases_repo.list_decisions(conn, orch_id)
    latest_by_id, _ = _latest_attempts(conn, orch_id)

    counts = {"success": 0, "failed": 0, "skipped_release": 0, "skipped_dep": 0}
    for j in jobs:
        rec = latest_by_id.get(j["id"])
        if rec and rec["status"] in counts:
            counts[rec["status"]] += 1

    decided_job_ids = {d["job_id"] for d in decisions}
    pending = [
        {"job_id": j["id"], "job_name": j["name"]}
        for j in jobs
        if j["requires_release"]
        and j["id"] not in decided_job_ids
        and latest_by_id.get(j["id"], {}).get("status") == "skipped_release"
    ]
    layers = topological_layers(jobs)
    return {
        "orchestration_id": orch_id,
        "job_total": len(jobs),
        "succeeded": counts["success"],
        "failed": counts["failed"],
        "skipped_release": counts["skipped_release"],
        "skipped_dep": counts["skipped_dep"],
        "attempt_count": len(orch_repo.list_attempts(conn, orch_id)),
        "released": sum(1 for d in decisions if d["decision"] == "released"),
        "rejected": sum(1 for d in decisions if d["decision"] == "rejected"),
        "release_pending_jobs": pending,
        "watermarked_jobs": conn.execute(
            "SELECT COUNT(*) c FROM watermarks WHERE watermark_ts IS NOT NULL"
        ).fetchone()["c"],
        "layer_count": len(layers),
    }


def _settle(conn: sqlite3.Connection, orch_id: int) -> dict[str, Any]:
    report = _build_report(conn, orch_id)
    if report["release_pending_jobs"]:
        status = "awaiting_release"
    else:
        status = "ok" if report["failed"] == 0 else "partial"
    orch_repo.finish_orchestration(
        conn, orch_id, status, report, finished=status != "awaiting_release"
    )
    conn.commit()
    return orch_repo.get_orchestration(conn, orch_id) or report


def run_orchestration() -> dict[str, Any]:
    """开一趟新编排：跑到放行点停下（awaiting_release），无闸则直接跑完。"""
    conn = connect()
    try:
        orch_id = orch_repo.create_orchestration(conn)
        conn.commit()
        _execute_pass(conn, orch_id)
        return _settle(conn, orch_id)
    finally:
        conn.close()


def resume_orchestration(orch_id: int) -> dict[str, Any]:
    """继续一趟停在放行点的编排：按已记录的放行/拒绝决定重评剩余作业。"""
    conn = connect()
    try:
        orch = orch_repo.get_orchestration(conn, orch_id)
        if not orch:
            raise LookupError("orchestration not found")
        if orch["status"] != "awaiting_release":
            raise ValueError(
                f"orchestration #{orch_id} is {orch['status']}, not awaiting_release"
            )
        _execute_pass(conn, orch_id)
        return _settle(conn, orch_id)
    finally:
        conn.close()


def decide_release(
    orch_id: int, job_id: int, decision: str, actor: str, note: str = ""
) -> dict[str, Any]:
    """记录一次可审计的放行/拒绝。决定一旦写入不可更改（每趟每作业唯一）。"""
    conn = connect()
    try:
        orch = orch_repo.get_orchestration(conn, orch_id)
        if not orch:
            raise LookupError("orchestration not found")
        if orch["status"] != "awaiting_release":
            raise ValueError(
                f"orchestration #{orch_id} is {orch['status']}; "
                "decisions are only accepted while awaiting_release"
            )
        job = jobs_repo.get_job(conn, job_id)
        if not job:
            raise LookupError("job not found")
        if not job["requires_release"]:
            raise ValueError(f"job {job['name']} does not require release")
        try:
            releases_repo.insert_decision(
                conn,
                orchestration_id=orch_id,
                job_id=job_id,
                decision=decision,
                actor=actor,
                note=note,
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                f"decision already recorded for job {job['name']} in orchestration #{orch_id}"
            ) from None
        conn.commit()
        return releases_repo.get_decision(conn, orch_id, job_id) or {}
    finally:
        conn.close()


def list_releases(orch_id: int) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return releases_repo.list_decisions(conn, orch_id)
    finally:
        conn.close()
