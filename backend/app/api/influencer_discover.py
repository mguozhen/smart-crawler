"""HTTP API for influencer discovery — Apify-compatible run/dataset lifecycle.

Endpoints:
  POST /discover/runs                          create run (returns runId + datasetId)
  GET  /discover/runs/{runId}                  run status
  GET  /discover/datasets/{datasetId}/items    dataset items (paginated)

Runs execute in a FastAPI BackgroundTask; state lives in RunRegistry. Caller
should poll runs/{id} until status is SUCCEEDED or FAILED, then GET items.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from ..influencers.discover import dispatch
from ..influencers.run_registry import REGISTRY, RunStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/discover", tags=["influencer-discover"])

_SUPPORTED = {"tiktok", "instagram", "facebook", "youtube_about"}


class RunRequest(BaseModel):
    platform: str
    hashtags: list[str] | None = None
    urls: list[str] | None = None
    limit: int = Field(default=38, ge=1, le=200)


class RunCreated(BaseModel):
    runId: str
    datasetId: str
    status: str


class RunStatusResponse(BaseModel):
    status: str
    itemCount: int
    error: str | None
    startedAt: str
    finishedAt: str | None


def _execute_run(rid: str, platform: str, params: dict, limit: int) -> None:
    REGISTRY.mark_running(rid)
    try:
        items = dispatch(platform, params, limit)
        REGISTRY.mark_succeeded(rid, items=items)
    except ValueError as e:
        REGISTRY.mark_failed(rid, error=str(e))
    except Exception as e:  # pylint: disable=broad-except
        log.exception("discover run %s failed", rid)
        REGISTRY.mark_failed(rid, error=f"{type(e).__name__}: {e}")


@router.post("/runs", response_model=RunCreated)
def create_run(req: RunRequest, background: BackgroundTasks) -> RunCreated:
    if req.platform not in _SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail=f"unknown platform: {req.platform}. Supported: {sorted(_SUPPORTED)}",
        )
    params = {
        "hashtags": req.hashtags or [],
        "urls": req.urls or [],
    }
    rid = REGISTRY.create_run()
    background.add_task(_execute_run, rid, req.platform, params, req.limit)
    return RunCreated(runId=rid, datasetId=rid, status=RunStatus.PENDING)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    r = REGISTRY.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return RunStatusResponse(**r)


@router.get("/datasets/{dataset_id}/items")
def get_items(
    dataset_id: str,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
):
    if REGISTRY.get_run(dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return REGISTRY.get_items(dataset_id, limit=limit, offset=offset)
