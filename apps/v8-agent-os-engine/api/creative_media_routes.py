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


@router.get("/recipe-libraries")
async def get_creative_media_recipe_libraries():
    return creative_media_runtime.recipe_libraries()


@router.get("/model-preferences")
async def get_creative_media_model_preferences():
    return creative_media_runtime.get_model_preferences()


@router.post("/model-preferences")
async def save_creative_media_model_preferences(body: dict = Body(...)):
    try:
        return creative_media_runtime.save_model_preferences(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/recipes/compile")
async def compile_creative_media_recipe(body: dict = Body(...)):
    try:
        recipe = creative_media_runtime.compile_recipe(body)
        return {"recipe": recipe}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/recipes")
async def list_creative_media_recipes(modality: str | None = None, recipeKind: str | None = None):
    return {"recipes": creative_media_runtime.list_recipes(modality=modality, recipe_kind=recipeKind)}


@router.get("/recipes/{recipe_id}")
async def get_creative_media_recipe(recipe_id: str):
    recipe = creative_media_runtime.get_recipe(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="creative media recipe not found")
    return {"recipe": recipe}


@router.post("/work-orders/compile")
async def compile_creative_media_work_order(body: dict = Body(...)):
    try:
        return {"workOrder": creative_media_runtime.compile_work_order(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/work-orders")
async def list_creative_media_work_orders(status: str | None = None, requestingRuntime: str | None = None):
    return {
        "workOrders": creative_media_runtime.list_work_orders(
            status=status,
            requesting_runtime=requestingRuntime,
        )
    }


@router.post("/assets")
async def register_creative_media_asset(body: dict = Body(...)):
    try:
        asset = creative_media_runtime.register_asset(body)
        return {"asset": asset}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/assets")
async def list_creative_media_assets(modality: str | None = None, role: str | None = None):
    return {"assets": creative_media_runtime.list_assets(modality=modality, role=role)}


@router.post("/character-bibles")
async def create_creative_media_character_bible(body: dict = Body(...)):
    try:
        return {"characterBible": creative_media_runtime.create_character_bible(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/character-bibles")
async def list_creative_media_character_bibles():
    return {"characterBibles": creative_media_runtime.list_character_bibles()}


@router.get("/character-bibles/{bible_id}")
async def get_creative_media_character_bible(bible_id: str):
    bible = creative_media_runtime.get_character_bible(bible_id)
    if not bible:
        raise HTTPException(status_code=404, detail="creative media character bible not found")
    return {"characterBible": bible}


@router.post("/keyframes")
async def register_creative_media_keyframe(body: dict = Body(...)):
    try:
        return {"keyframe": creative_media_runtime.register_keyframe(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/keyframes")
async def list_creative_media_keyframes(
    recipeId: str | None = None,
    role: str | None = None,
    characterBibleId: str | None = None,
):
    return {
        "keyframes": creative_media_runtime.list_keyframes(
            recipe_id=recipeId,
            role=role,
            character_bible_id=characterBibleId,
        )
    }


@router.get("/keyframes/{keyframe_id}")
async def get_creative_media_keyframe(keyframe_id: str):
    keyframe = creative_media_runtime.get_keyframe(keyframe_id)
    if not keyframe:
        raise HTTPException(status_code=404, detail="creative media keyframe not found")
    return {"keyframe": keyframe}


@router.post("/edit-plans")
async def create_creative_media_edit_plan(body: dict = Body(...)):
    try:
        return {"editPlan": creative_media_runtime.create_edit_plan(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/edit-plans")
async def list_creative_media_edit_plans(recipeId: str | None = None):
    return {"editPlans": creative_media_runtime.list_edit_plans(recipe_id=recipeId)}


@router.get("/edit-plans/{plan_id}")
async def get_creative_media_edit_plan(plan_id: str):
    plan = creative_media_runtime.get_edit_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="creative media edit plan not found")
    return {"editPlan": plan}


@router.post("/renders")
async def render_creative_media_edit_plan(body: dict = Body(...)):
    try:
        return {"render": creative_media_runtime.render_edit_plan(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/renders")
async def list_creative_media_renders(planId: str | None = None, status: str | None = None):
    return {"renders": creative_media_runtime.list_renders(plan_id=planId, status=status)}


@router.get("/renders/{render_job_id}")
async def get_creative_media_render(render_job_id: str):
    render = creative_media_runtime.get_render(render_job_id)
    if not render:
        raise HTTPException(status_code=404, detail="creative media render job not found")
    return {"render": render}


@router.post("/jobs")
async def create_creative_media_job(body: dict = Body(...)):
    try:
        job = await creative_media_runtime.create_job(body)
        return {"job": job}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs")
async def list_creative_media_jobs(modality: str | None = None, status: str | None = None):
    return {"jobs": creative_media_runtime.list_jobs(modality=modality, status=status)}


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


@router.post("/quality-jobs")
async def create_creative_media_quality_job(body: dict = Body(...)):
    try:
        return {"qualityJob": creative_media_runtime.create_quality_job(body)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/quality-jobs")
async def list_creative_media_quality_jobs(status: str | None = None):
    return {"qualityJobs": creative_media_runtime.list_quality_jobs(status=status)}


@router.get("/quality-jobs/{quality_job_id}")
async def get_creative_media_quality_job(quality_job_id: str):
    quality_job = creative_media_runtime.get_quality_job(quality_job_id)
    if not quality_job:
        raise HTTPException(status_code=404, detail="creative media quality job not found")
    return {"qualityJob": quality_job}


@router.post("/jobs/{job_id}/retry")
async def retry_creative_media_job(job_id: str, body: dict = Body(default_factory=dict)):
    try:
        job = await creative_media_runtime.retry_job(job_id, body)
        return {"job": job}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/cost-ledger")
async def get_creative_media_cost_ledger():
    return {"entries": creative_media_runtime.list_cost_ledger()}


@router.get("/safety-events")
async def get_creative_media_safety_events():
    return {"events": creative_media_runtime.list_safety_events()}
