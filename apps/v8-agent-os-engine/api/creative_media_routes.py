from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from runtimes.creative_media.runtime import creative_media_runtime


router = APIRouter(prefix="/creative-media", tags=["creative-media"])


@router.get("/catalog")
async def get_creative_media_catalog():
    return creative_media_runtime.catalog()


@router.get("/resolutions")
async def get_creative_media_resolutions():
    return creative_media_runtime.resolutions()


@router.post("/jobs")
async def create_creative_media_job(body: dict = Body(...)):
    try:
        job = await creative_media_runtime.create_job(body)
        return {"job": job}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs/{job_id}")
async def get_creative_media_job(job_id: str, refresh: bool = True):
    try:
        job = await creative_media_runtime.refresh_job(job_id) if refresh else creative_media_runtime.get_job(job_id, refresh=False)
        if not job:
            raise HTTPException(status_code=404, detail="creative media job not found")
        return {"job": job}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs/{job_id}/artifacts")
async def get_creative_media_job_artifacts(job_id: str):
    job = creative_media_runtime.get_job(job_id, refresh=False)
    if not job:
        raise HTTPException(status_code=404, detail="creative media job not found")
    return {"artifacts": creative_media_runtime.job_artifacts(job_id)}
