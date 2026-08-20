import asyncio
from copy import deepcopy
from time import monotonic
from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from core.storage import storage

from .agent_browser_routes import open_agent_browser_profile

from .models import (
    ComputerUseAgentBrowserOpenPayload,
    ComputerUseAppQueryPayload,
    ComputerUseClickPayload,
    ComputerUseElementQueryPayload,
    ComputerUseHotkeyPayload,
    ComputerUseObservePayload,
    ComputerUseScreenshotPayload,
    ComputerUseScrollPayload,
    ComputerUseTypePayload,
    ComputerUseWaitPayload,
    ComputerUseWindowQueryPayload,
)


router = APIRouter()

_AVAILABILITY_CACHE_TTL_SECONDS = 30.0
_AVAILABILITY_FAILURE_RETRY_SECONDS = 10.0
_AVAILABILITY_EXPLICIT_REFRESH_TIMEOUT_SECONDS = 30.0
_availability_cache: dict[str, Any] | None = None
_availability_cache_at = 0.0
_availability_last_error: str | None = None
_availability_last_failure_at = 0.0
_availability_lock = asyncio.Lock()
_availability_refresh_task: asyncio.Task[dict[str, Any]] | None = None


def _computer_use_runtime():
    from runtimes.computer_use.runtime import computer_use_runtime

    return computer_use_runtime


def _browser_session_service():
    from runtimes.computer_use.browser_session_service import browser_session_service

    return browser_session_service


def _browser_session_http_error(exc: Exception) -> HTTPException:
    from runtimes.computer_use.browser_session_service import BrowserSessionError

    if isinstance(exc, BrowserSessionError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=500, detail="Workbench browser session failed")


def _build_computer_use_availability() -> dict[str, Any]:
    return dict(_computer_use_runtime().availability() or {})


def _configuration_snapshot() -> dict[str, Any]:
    try:
        config = dict(storage.get_computer_use_config() or {})
        browser_lane = dict(config.get("browserLane") or {})
        return {
            "available": None,
            "details": {
                "configuration": {
                    "browserLaneEnabled": bool(browser_lane.get("enabled", False)),
                    "browserLaneMode": str(browser_lane.get("mode") or "auto_if_available"),
                    "browserLaneProvider": str(browser_lane.get("provider") or "engine_managed_cdp"),
                }
            },
        }
    except Exception as exc:
        return {
            "available": None,
            "details": {
                "configuration": {
                    "available": False,
                    "errorClass": exc.__class__.__name__,
                }
            },
        }


def _availability_response(
    payload: dict[str, Any],
    *,
    state: str,
    stale: bool,
    error: str | None = None,
) -> dict[str, Any]:
    response = deepcopy(payload)
    response["environmentProbe"] = {
        "state": state,
        "stale": stale,
        "refreshing": state == "refreshing",
        "error": error,
    }
    return response


async def _refresh_computer_use_availability() -> dict[str, Any]:
    global _availability_cache, _availability_cache_at
    global _availability_last_error, _availability_last_failure_at

    async with _availability_lock:
        try:
            payload = await asyncio.to_thread(_build_computer_use_availability)
            _availability_cache = dict(payload or {})
            _availability_cache_at = monotonic()
            _availability_last_error = None
            _availability_last_failure_at = 0.0
            return _availability_response(_availability_cache, state="fresh", stale=False)
        except Exception as exc:
            _availability_last_error = f"{exc.__class__.__name__}: {exc}"
            _availability_last_failure_at = monotonic()
            fallback = _availability_cache if _availability_cache is not None else _configuration_snapshot()
            return _availability_response(
                fallback,
                state="failed",
                stale=_availability_cache is not None,
                error=_availability_last_error,
            )


def _ensure_availability_refresh() -> asyncio.Task[dict[str, Any]]:
    global _availability_refresh_task

    if _availability_refresh_task is None or _availability_refresh_task.done():
        _availability_refresh_task = asyncio.create_task(_refresh_computer_use_availability())
    return _availability_refresh_task


def _compat_invocation_metadata(endpoint: str) -> dict:
    return {
        "triggerSource": "computer_use_compat_http",
        "invocationSource": "compat_http",
        "executionIntent": "debug_primitive",
        "routeKind": "compat_debug",
        "endpoint": endpoint,
        "promotionAllowed": False,
    }


def _real_host_matrix_service():
    from runtimes.computer_use.real_host_matrix import (
        ingest_real_host_matrix,
        read_latest_real_host_matrix,
    )

    return read_latest_real_host_matrix, ingest_real_host_matrix


@router.get("/computer-use/availability")
async def get_computer_use_availability(refresh: bool = False):
    now = monotonic()
    cache_is_fresh = (
        _availability_cache is not None
        and (now - _availability_cache_at) <= _AVAILABILITY_CACHE_TTL_SECONDS
    )
    if cache_is_fresh and not refresh:
        return _availability_response(_availability_cache or {}, state="fresh", stale=False)

    if refresh:
        refresh_task = _ensure_availability_refresh()
        try:
            return deepcopy(
                await asyncio.wait_for(
                    asyncio.shield(refresh_task),
                    timeout=_AVAILABILITY_EXPLICIT_REFRESH_TIMEOUT_SECONDS,
                )
            )
        except TimeoutError:
            fallback = _availability_cache if _availability_cache is not None else _configuration_snapshot()
            return _availability_response(
                fallback,
                state="refreshing",
                stale=_availability_cache is not None,
                error=f"refresh_timeout_{_AVAILABILITY_EXPLICIT_REFRESH_TIMEOUT_SECONDS:g}s",
            )

    retry_blocked = (
        _availability_last_failure_at > 0
        and (now - _availability_last_failure_at) < _AVAILABILITY_FAILURE_RETRY_SECONDS
    )
    if not retry_blocked:
        _ensure_availability_refresh()

    if _availability_cache is not None:
        return _availability_response(
            _availability_cache,
            state="failed" if retry_blocked else "refreshing",
            stale=True,
            error=_availability_last_error,
        )
    return _availability_response(
        _configuration_snapshot(),
        state="failed" if retry_blocked else "refreshing",
        stale=False,
        error=_availability_last_error,
    )


@router.post("/computer-use/agent-browser/open", deprecated=True)
async def open_computer_use_agent_browser(payload: ComputerUseAgentBrowserOpenPayload):
    try:
        return await asyncio.to_thread(
            open_agent_browser_profile,
            url=payload.url or "about:blank",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/workbench/browser-sessions")
async def create_workbench_browser_session(session_id: str, body: dict[str, Any] = Body(default={})):
    try:
        runtime = _computer_use_runtime()
        provider_factory = getattr(runtime, "workbench_browser_provider", None)
        provider = provider_factory() if callable(provider_factory) else None
        if provider is None:
            raise RuntimeError("Computer Use browser automation provider is unavailable")
        return await asyncio.to_thread(
            _browser_session_service().create_session,
            session_id=session_id,
            provider=provider,
            url=str(body.get("url") or "about:blank"),
            browser_kind=str(body.get("browserKind") or "auto"),
            focus_requested=bool(body.get("focusRequested", True)),
            user_initiated=bool(body.get("userInitiated", True)),
        )
    except Exception as exc:
        raise _browser_session_http_error(exc) from exc


@router.post("/sessions/{session_id}/workbench/browser/prepare")
async def prepare_workbench_browser(session_id: str, body: dict[str, Any] = Body(default={})):
    try:
        if not str(session_id or "").strip():
            raise ValueError("sessionId is required")
        runtime = _computer_use_runtime()
        provider_factory = getattr(runtime, "workbench_browser_provider", None)
        provider = provider_factory() if callable(provider_factory) else None
        if provider is None:
            raise RuntimeError("Computer Use browser automation provider is unavailable")
        result = await asyncio.to_thread(
            provider.prepare_workbench_browser,
            browser_kind=str(body.get("browserKind") or "auto"),
        )
        return {
            "ok": bool(result.get("ok")),
            "ready": bool(result.get("ready")),
            "browserKind": str(result.get("browserKind") or "chromium"),
            "managedHeadless": bool(result.get("managedHeadless")),
        }
    except Exception as exc:
        raise _browser_session_http_error(exc) from exc


@router.get("/workbench/browser-sessions/{browser_session_id}")
async def get_workbench_browser_session(browser_session_id: str):
    try:
        return await asyncio.to_thread(
            _browser_session_service().public_status,
            browser_session_id,
        )
    except Exception as exc:
        raise _browser_session_http_error(exc) from exc


@router.post("/workbench/browser-sessions/{browser_session_id}/ws-ticket")
async def issue_workbench_browser_ws_ticket(browser_session_id: str):
    try:
        return _browser_session_service().issue_ws_ticket(browser_session_id)
    except Exception as exc:
        raise _browser_session_http_error(exc) from exc


@router.websocket("/workbench/browser-sessions/{browser_session_id}/ws")
async def workbench_browser_websocket(websocket: WebSocket, browser_session_id: str):
    ticket_value = str(websocket.query_params.get("ticket") or "").strip()
    try:
        ticket = _browser_session_service().consume_ws_ticket(browser_session_id, ticket_value)
    except Exception:
        await websocket.close(code=4403, reason="Invalid or expired browser ticket")
        return
    await websocket.accept()
    try:
        await _browser_session_service().serve_websocket(
            browser_session_id,
            ticket.client_id,
            websocket,
        )
    except WebSocketDisconnect:
        return


@router.delete("/workbench/browser-sessions/{browser_session_id}")
async def delete_workbench_browser_session(browser_session_id: str):
    try:
        return await asyncio.to_thread(
            _browser_session_service().delete_session,
            browser_session_id,
        )
    except Exception as exc:
        raise _browser_session_http_error(exc) from exc


@router.get("/computer-use/real-host-matrix")
async def get_computer_use_real_host_matrix():
    try:
        read_latest, _ingest = _real_host_matrix_service()
        return read_latest()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/real-host-matrix/ingest")
async def ingest_computer_use_real_host_matrix(payload: dict[str, Any] = Body(...)):
    try:
        _read_latest, ingest = _real_host_matrix_service()
        return ingest(dict(payload or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/apps")
async def list_computer_use_apps(payload: ComputerUseAppQueryPayload):
    try:
        return _computer_use_runtime().list_apps(
            query=payload.query,
            limit=max(1, min(payload.limit, 100)),
            include_running=payload.include_running,
            force_refresh=payload.force_refresh,
            include_learned=payload.include_learned,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/windows")
async def list_computer_use_windows(payload: ComputerUseWindowQueryPayload):
    try:
        return _computer_use_runtime().list_windows(
            title_filter=payload.title_filter,
            limit=max(1, min(payload.limit, 50)),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/observe")
async def observe_computer_use(payload: ComputerUseObservePayload):
    try:
        return _computer_use_runtime().observe(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            depth_limit=max(1, min(payload.depth_limit, 8)),
            element_limit=max(1, min(payload.element_limit, 150)),
            include_screenshot=payload.include_screenshot,
            invocation_metadata=_compat_invocation_metadata("observe"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/find-elements")
async def find_computer_use_elements(payload: ComputerUseElementQueryPayload):
    try:
        return _computer_use_runtime().find_elements(
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            name=payload.name,
            name_contains=payload.name_contains,
            automation_id=payload.automation_id,
            control_type=payload.control_type,
            class_name=payload.class_name,
            depth_limit=max(1, min(payload.depth_limit, 10)),
            limit=max(1, min(payload.limit, 50)),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/click")
async def computer_use_click(payload: ComputerUseClickPayload):
    try:
        return _computer_use_runtime().click(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            element_id=payload.element_id,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            name=payload.name,
            name_contains=payload.name_contains,
            automation_id=payload.automation_id,
            control_type=payload.control_type,
            class_name=payload.class_name,
            double=payload.double,
            invocation_metadata=_compat_invocation_metadata("actions/click"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/type")
async def computer_use_type(payload: ComputerUseTypePayload):
    try:
        return _computer_use_runtime().type_text(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            element_id=payload.element_id,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            name=payload.name,
            automation_id=payload.automation_id,
            control_type=payload.control_type,
            class_name=payload.class_name,
            text=payload.text,
            clear_first=payload.clear_first,
            press_enter=payload.press_enter,
            invocation_metadata=_compat_invocation_metadata("actions/type"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/hotkey")
async def computer_use_hotkey(payload: ComputerUseHotkeyPayload):
    try:
        return _computer_use_runtime().hotkey(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            sequence=payload.sequence,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            invocation_metadata=_compat_invocation_metadata("actions/hotkey"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/scroll")
async def computer_use_scroll(payload: ComputerUseScrollPayload):
    try:
        return _computer_use_runtime().scroll(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            amount=payload.amount,
            element_id=payload.element_id,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            invocation_metadata=_compat_invocation_metadata("actions/scroll"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/wait")
async def computer_use_wait(payload: ComputerUseWaitPayload):
    try:
        return _computer_use_runtime().wait_for_element(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            element_id=payload.element_id,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            name=payload.name,
            name_contains=payload.name_contains,
            automation_id=payload.automation_id,
            control_type=payload.control_type,
            class_name=payload.class_name,
            timeout_ms=payload.timeout_ms,
            poll_ms=payload.poll_ms,
            invocation_metadata=_compat_invocation_metadata("actions/wait"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/computer-use/actions/screenshot")
async def computer_use_screenshot(payload: ComputerUseScreenshotPayload):
    try:
        return _computer_use_runtime().capture_screenshot(
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            goal=payload.goal,
            element_id=payload.element_id,
            window_title=payload.window_title,
            window_handle=payload.window_handle,
            invocation_metadata=_compat_invocation_metadata("actions/screenshot"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
