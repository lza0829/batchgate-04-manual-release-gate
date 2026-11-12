from fastapi import APIRouter, HTTPException

from app.db import connect
from app.repositories import orchestrations as orch_repo
from app.schemas.entities import ReleaseDecisionBody
from app.services.orchestrate_service import (
    decide_release,
    list_releases,
    resume_orchestration,
    run_orchestration,
)

router = APIRouter(tags=["orchestrations"])


@router.get("/orchestrations")
def list_orchestrations():
    conn = connect()
    try:
        return orch_repo.list_orchestrations(conn)
    finally:
        conn.close()


@router.get("/orchestrations/{orch_id}")
def get_orchestration(orch_id: int):
    conn = connect()
    try:
        row = orch_repo.get_orchestration(conn, orch_id)
        if not row:
            raise HTTPException(404, "not found")
        return row
    finally:
        conn.close()


@router.post("/orchestrations")
def create_orchestration():
    return run_orchestration()


@router.post("/orchestrations/{orch_id}/resume")
def resume(orch_id: int):
    try:
        return resume_orchestration(orch_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/orchestrations/{orch_id}/releases")
def get_releases(orch_id: int):
    return list_releases(orch_id)


@router.post("/orchestrations/{orch_id}/releases")
def post_release(orch_id: int, body: ReleaseDecisionBody):
    try:
        return decide_release(
            orch_id, body.job_id, body.decision, body.actor, body.note
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
