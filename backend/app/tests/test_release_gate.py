"""人工放行闸：分阶段编排、放行/拒绝、下游阻断与报告对账。"""

import pytest

from app.db import connect
from app.repositories import jobs as jobs_repo
from app.repositories import orchestrations as orch_repo
from app.repositories import watermarks as wm_repo
from app.seed import init_db
from app.services import orchestrate_service as svc


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "test.db")
    init_db()
    yield tmp_path


def _job_id(name: str) -> int:
    conn = connect()
    try:
        return jobs_repo.get_job_by_name(conn, name)["id"]
    finally:
        conn.close()


def _attempts(orch_id: int):
    conn = connect()
    try:
        rows = orch_repo.list_attempts(conn, orch_id)
    finally:
        conn.close()
    latest = {}
    for a in rows:
        latest[a["job_name"]] = a
    return latest, rows


def _watermarks():
    conn = connect()
    try:
        return {w["job_name"]: w for w in wm_repo.list_watermarks(conn)}
    finally:
        conn.close()


def _assert_report_reconciles(orch):
    """报告计数 == 每作业最近一条尝试记录汇总 == 放行记录汇总。"""
    rep = orch["report"]
    latest, rows = _attempts(orch["id"])
    assert rep["attempt_count"] == len(rows)
    assert (
        rep["succeeded"] + rep["failed"] + rep["skipped_release"] + rep["skipped_dep"]
        == rep["job_total"]
    )
    conn = connect()
    try:
        decisions = svc.list_releases(orch["id"])
    finally:
        conn.close()
    assert rep["released"] == sum(1 for d in decisions if d["decision"] == "released")
    assert rep["rejected"] == sum(1 for d in decisions if d["decision"] == "rejected")
    return latest


def test_run_pauses_at_gate_unreleased_skip(db):
    orch = svc.run_orchestration()
    assert orch["status"] == "awaiting_release"
    assert orch["finished_at"] is None
    rep = orch["report"]
    assert rep["succeeded"] == 2
    assert rep["skipped_release"] == 1
    assert [p["job_name"] for p in rep["release_pending_jobs"]] == ["fact_order_daily"]
    latest = _assert_report_reconciles(orch)
    fact = latest["fact_order_daily"]
    assert fact["status"] == "skipped_release"
    assert fact["detail"]["gate"] == "pending"
    # 未放行不写水位
    assert _watermarks()["fact_order_daily"]["watermark_ts"] is None


def test_release_then_resume_runs_success(db):
    orch = svc.run_orchestration()
    dec = svc.decide_release(orch["id"], _job_id("fact_order_daily"), "released", "alice", "看过上游产出，放行")
    assert dec["actor"] == "alice" and dec["decided_at"]
    done = svc.resume_orchestration(orch["id"])
    assert done["status"] == "ok"
    assert done["finished_at"] is not None
    rep = done["report"]
    assert rep["succeeded"] == 3 and rep["released"] == 1
    assert rep["release_pending_jobs"] == []
    latest = _assert_report_reconciles(done)
    fact = latest["fact_order_daily"]
    assert fact["status"] == "success"
    assert fact["attempt_no"] == 2  # 第 1 次是放行跳过，放行后第 2 次真正执行
    assert fact["detail"]["gate"] == "released"
    assert fact["detail"]["decision_id"] == dec["id"]
    assert _watermarks()["fact_order_daily"]["watermark_ts"] is not None


def test_reject_blocks_job_for_the_run(db):
    orch = svc.run_orchestration()
    svc.decide_release(orch["id"], _job_id("fact_order_daily"), "rejected", "bob", "产出有异味")
    done = svc.resume_orchestration(orch["id"])
    rep = done["report"]
    assert rep["rejected"] == 1
    assert rep["skipped_release"] == 1
    latest = _assert_report_reconciles(done)
    fact = latest["fact_order_daily"]
    assert fact["status"] == "skipped_release"
    assert fact["detail"]["gate"] == "rejected"
    assert "bob" in fact["detail"]["reason"]
    assert fact["detail"]["note"] == "产出有异味"
    assert _watermarks()["fact_order_daily"]["watermark_ts"] is None


def test_downstream_of_unreleased_gate_is_skipped_dep(db):
    conn = connect()
    try:
        jobs_repo.update_release_gate(conn, _job_id("ingest_orders"), True)
        conn.commit()
    finally:
        conn.close()
    orch = svc.run_orchestration()
    latest = _assert_report_reconciles(orch)
    assert latest["ingest_orders"]["status"] == "skipped_release"
    fact = latest["fact_order_daily"]
    assert fact["status"] == "skipped_dep"
    assert "ingest_orders" in fact["detail"]["reason"]
    assert fact["detail"]["upstream_status"]["ingest_orders"] == "skipped_release"

    # 先放行 ingest_orders：fact 仍卡在自己的闸上（分阶段继续）
    svc.decide_release(orch["id"], _job_id("ingest_orders"), "released", "alice")
    mid = svc.resume_orchestration(orch["id"])
    assert mid["status"] == "awaiting_release"
    # 再放行 fact_order_daily：全图跑通
    svc.decide_release(orch["id"], _job_id("fact_order_daily"), "released", "alice")
    done = svc.resume_orchestration(orch["id"])
    assert done["status"] == "ok"
    assert done["report"]["succeeded"] == 3
    _assert_report_reconciles(done)


def test_decision_and_resume_validation(db):
    orch = svc.run_orchestration()
    with pytest.raises(ValueError, match="does not require release"):
        svc.decide_release(orch["id"], _job_id("ingest_orders"), "released", "alice")
    svc.decide_release(orch["id"], _job_id("fact_order_daily"), "released", "alice")
    with pytest.raises(ValueError, match="already recorded"):
        svc.decide_release(orch["id"], _job_id("fact_order_daily"), "rejected", "bob")
    done = svc.resume_orchestration(orch["id"])
    assert done["status"] == "ok"
    with pytest.raises(ValueError, match="awaiting_release"):
        svc.decide_release(orch["id"], _job_id("fact_order_daily"), "released", "alice")
    with pytest.raises(ValueError, match="awaiting_release"):
        svc.resume_orchestration(orch["id"])
    with pytest.raises(LookupError):
        svc.resume_orchestration(9999)


def test_resume_without_decision_is_noop(db):
    orch = svc.run_orchestration()
    again = svc.resume_orchestration(orch["id"])
    assert again["status"] == "awaiting_release"
    # 没有新决定时不重复写跳过记录
    assert again["report"]["attempt_count"] == orch["report"]["attempt_count"]
