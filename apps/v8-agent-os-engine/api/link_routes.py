from __future__ import annotations

from fastapi import APIRouter, Request

from core.v8_link import build_link_manifest, build_mesh_provider_status, build_vpn_diagnostics


router = APIRouter()


@router.get("/link/manifest")
async def get_link_manifest(request: Request):
    origin = str(request.headers.get("origin") or "").strip() or None
    return build_link_manifest(request_admin_origin=origin)


@router.get("/link/diagnostics")
async def get_link_diagnostics():
    manifest = build_link_manifest()
    admin_base = str((manifest.get("admin") or {}).get("baseUrl") or "").strip()
    engine_base = str((manifest.get("engine") or {}).get("baseUrl") or "").strip()
    return build_vpn_diagnostics(admin_base_url=admin_base, engine_base_url=engine_base)


@router.get("/link/mesh/status")
async def get_link_mesh_status():
    manifest = build_link_manifest()
    admin_base = str((manifest.get("admin") or {}).get("baseUrl") or "").strip()
    engine_base = str((manifest.get("engine") or {}).get("baseUrl") or "").strip()
    return build_mesh_provider_status(admin_base_url=admin_base, engine_base_url=engine_base)
