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
