"""Finite appearance uploads and owned asset reads; no Admin process involved."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.datastructures import UploadFile

from core.auth_context import is_local_client, require_client_principal
from core.client_identity import IdentityError, get_identity_service, public_user
from core.client_user_media import background_receipt, media_path, remove_unreferenced

router = APIRouter(tags=["client-assets"])
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.get("/api/client/web-surface")
async def web_surface(request: Request):
    from core.auth_context import require_local_management
    require_local_management(request)
    parsed = urlsplit(os.environ.get("V8_WEB_BASE_URL") or "http://127.0.0.1:9527")
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise HTTPException(503, "web_surface_configuration_invalid")
    return RedirectResponse(f"{parsed.scheme}://{parsed.netloc}/chat", status_code=307, headers={"Cache-Control": "no-store"})


def _convert_image(source: Path, target: Path, *, avatar: bool) -> tuple[int, int]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise HTTPException(503, "image_processing_unavailable") from None
    try:
        with Image.open(source) as raw:
            # Reject claimed image MIME whose decoded format belongs elsewhere.
            if raw.format not in {"JPEG", "PNG", "WEBP", *( ["GIF"] if avatar else [])}:
                raise HTTPException(400, "image_format_invalid")
            if raw.width * raw.height > 40_000_000:
                raise HTTPException(413, "image_dimensions_too_large")
            original = raw.size
            image = ImageOps.exif_transpose(raw).convert("RGBA" if "A" in raw.getbands() else "RGB")
            if avatar:
                image = ImageOps.fit(image, (256, 256), method=Image.Resampling.LANCZOS)
            else:
                image.thumbnail((3840, 2160), Image.Resampling.LANCZOS)
            image.save(target, "WEBP", quality=90 if avatar else 88)
            if not avatar:
                image.thumbnail((256, 144), Image.Resampling.LANCZOS)
                image.save(target.with_suffix(".thumb.webp"), "WEBP", quality=80)
            return original
    except Image.DecompressionBombError:
        raise HTTPException(413, "image_dimensions_too_large") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "image_content_invalid") from None


async def _copy_upload(request: Request, file: UploadFile | None, target: Path, limit: int) -> bytes:
    size, signature = 0, b""
    with target.open("xb") as output:
        if file is not None:
            async def chunks():
                while chunk := await file.read(1024 * 1024):
                    yield chunk
            stream = chunks()
        else:
            stream = request.stream()
        async for chunk in stream:
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "user_media_too_large")
            signature = (signature + chunk[:12])[:12]
            await asyncio.to_thread(output.write, chunk)
    if size == 0:
        raise HTTPException(400, "upload_empty")
    return signature


async def upload_media(request: Request, *, avatar: bool):
    principal = require_client_principal(request)
    service = get_identity_service()
    kind = "avatar" if avatar else "background"
    form = None
    target = temporary = None
    committed = False
    try:
        raw = not avatar and request.headers.get("x-v8-upload-mode") == "raw"
        if raw:
            file = None
            media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        else:
            form = await request.form()
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(400, "upload_file_required")
            media_type = file.content_type or ""
        video = media_type == "video/mp4" and not avatar
        if media_type not in IMAGE_TYPES and not video and not (avatar and media_type == "image/gif"):
            raise HTTPException(400, "user_media_type_invalid")
        limit = 8 * 1024**2 if avatar else (500 if video else 20) * 1024**2
        if not is_local_client(principal):
            limit = min(limit, 50 * 1024**2 - 1)
        filename = f"{'user' if avatar else 'background'}-{uuid.uuid4().hex}.{'mp4' if video else 'webp'}"
        target = media_path(service.owners.home, kind, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".upload")
        signature = await _copy_upload(request, file, temporary, limit)
        width = height = None
        if video:
            if len(signature) < 12 or signature[4:8] != b"ftyp":
                raise HTTPException(400, "mp4_content_invalid")
            temporary.replace(target)
        else:
            conversion = asyncio.create_task(asyncio.to_thread(_convert_image, temporary, target, avatar=avatar))
            try:
                width, height = await asyncio.shield(conversion)
            except asyncio.CancelledError:
                # A Python thread cannot be force-cancelled. Finish it before
                # cleanup so it cannot recreate a file after cancellation.
                try:
                    await conversion
                except Exception:
                    pass
                raise
        url = f"/user-assets/{kind}/{filename}"
        result = {"url": url, "path": url, "mediaType": "video" if video else "image", "originalWidth": width, "originalHeight": height}
        if not avatar and request.headers.get("x-v8-background-intent") == "playlist":
            result["receipt"] = background_receipt(service.owners, principal.subject, url, result["mediaType"], now=service.clock())
        else:
            previous = service.owners.owner()
            patch = {"image": url} if avatar else {"appearance": {"lightBackgroundMedia": url,
                "lightBackgroundMediaType": result["mediaType"], "lightBackgroundImage": "" if video else url, "lightBackgroundEnabled": True}}
            updated = service.owners.update(principal.subject, patch, now=service.clock())
            result["user"] = service.client_user(principal)
            previous_url = previous.get("image") if avatar else (previous.get("appearance") or {}).get("lightBackgroundMedia", "")
            if previous_url:
                remove_unreferenced(service.owners, previous_url, kind=kind)
        committed = True
        if not is_local_client(principal):
            from core.client_identity.resources import sign_resource_url
            result["url"] = sign_resource_url(service, url, principal)
        if avatar:
            result.update(width=256, height=256)
        return result
    except IdentityError as exc:
        raise HTTPException(exc.status, exc.code) from None
    finally:
        if form is not None:
            await form.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if target is not None and not committed:
            target.unlink(missing_ok=True)
            target.with_suffix(".thumb.webp").unlink(missing_ok=True)


@router.post("/api/client/user-avatar-upload")
async def avatar_upload(request: Request):
    return await upload_media(request, avatar=True)


@router.post("/api/client/user-background-upload")
async def background_upload(request: Request):
    return await upload_media(request, avatar=False)


@router.get("/api/client/music")
async def music(request: Request):
    require_client_principal(request)
    from core.storage import storage
    config = storage.get_music_config() or {}
    tracks = config.get("tracks", []) if isinstance(config, dict) else config
    return sorted([item for item in tracks if isinstance(item, dict)], key=lambda item: item.get("order") or 0)


@router.api_route("/user-assets/{kind}/{filename}", methods=["GET", "HEAD"])
async def user_asset(request: Request, kind: str, filename: str):
    from core.client_identity.resources import verify_resource_request
    service = get_identity_service()
    path = request.url.path + ("?" + request.url.query if request.url.query else "")
    principal = verify_resource_request(service, path, method=request.method) or require_client_principal(request)
    if request.scope.get("state", {}).get("phone_gateway") and is_local_client(principal):
        raise HTTPException(403, "local_client_not_allowed_on_phone_gateway")
    try:
        target = media_path(get_identity_service().owners.home, kind, filename)
    except ValueError:
        raise HTTPException(404, "user_asset_not_found") from None
    if not target.is_file():
        raise HTTPException(404, "user_asset_not_found")
    return FileResponse(target, media_type="video/mp4" if target.suffix == ".mp4" else "image/webp", headers={"Cache-Control": "private, max-age=86400"})
