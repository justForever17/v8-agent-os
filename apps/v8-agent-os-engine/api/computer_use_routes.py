from fastapi import APIRouter, HTTPException

from .models import (
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


def _computer_use_runtime():
    from runtimes.computer_use.runtime import computer_use_runtime

    return computer_use_runtime


def _compat_invocation_metadata(endpoint: str) -> dict:
    return {
        "triggerSource": "computer_use_compat_http",
        "invocationSource": "compat_http",
        "executionIntent": "debug_primitive",
        "routeKind": "compat_debug",
        "endpoint": endpoint,
        "promotionAllowed": False,
    }


@router.get("/computer-use/availability")
async def get_computer_use_availability():
    try:
        return _computer_use_runtime().availability()
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
