import os
from core.storage import storage
from core.action_executor import ActionExecutor
from erc.runtime_context import get_runtime_context

class HooksManager:
    """
    Manages deterministic hooks that can automate workflows and inject custom logic
    around various points within the engine's operational lifecycle.
    """
    
    def execute_hook(self, event_name: str, **kwargs):
        """
        Executes all enabled hooks registered for the given event name synchronously.
        If a hook is configured as `async: true`, it will use asyncio.create_task to background it.
        """
        config = storage.get_hooks_config()
        hooks = config.get("hooks", [])
        exclude_targets = {str(item).strip() for item in (kwargs.pop("exclude_targets", None) or []) if str(item).strip()}
        exclude_names = {str(item).strip() for item in (kwargs.pop("exclude_names", None) or []) if str(item).strip()}
        
        for hook_cfg in hooks:
            hook = hook_cfg if isinstance(hook_cfg, dict) else hook_cfg
            
            if not hook.get("enabled", False):
                continue
            if str(hook.get("target") or "").strip() in exclude_targets:
                continue
            if str(hook.get("name") or "").strip() in exclude_names:
                continue
            
            hook_events = hook.get("events", [])
            if isinstance(hook_events, str):
                hook_events = [hook_events]
                
            if event_name not in hook_events and "*" not in hook_events:
                continue
                
            hook_type = hook.get("type", "command")
            target = hook.get("target")
            is_async = hook.get("async", False)
            
            if not target:
                continue
                
            try:
                execution_kwargs = dict(kwargs)
                runtime_context = get_runtime_context()
                for key in (
                    "user_id",
                    "project_id",
                    "workspace_id",
                    "workspace_path",
                    "resolved_scope",
                    "scope_source",
                    "scope_chain",
                ):
                    if execution_kwargs.get(key) is None and runtime_context.get(key) is not None:
                        execution_kwargs[key] = runtime_context.get(key)
                context_session_id = runtime_context.get("session_id")
                context_run_id = runtime_context.get("run_id")
                if execution_kwargs.get("session_id") is None and context_session_id is not None:
                    execution_kwargs.setdefault("parent_session_id", context_session_id)
                    execution_kwargs.setdefault("source_session_id", context_session_id)
                if execution_kwargs.get("run_id") is None and context_run_id is not None:
                    execution_kwargs.setdefault("parent_run_id", context_run_id)
                    execution_kwargs.setdefault("source_run_id", context_run_id)
                execution_kwargs.setdefault("trigger", f"hook:{event_name}")
                execution_kwargs.setdefault("task_name", hook.get("name") or f"hook:{event_name}")
                execution_kwargs.setdefault("hook_name", hook.get("name"))
                execution_kwargs.setdefault("hook_target", target)
                if is_async and execution_kwargs.get("session_id"):
                    execution_kwargs.setdefault("parent_session_id", execution_kwargs["session_id"])
                    execution_kwargs.pop("session_id", None)
                ActionExecutor.execute(
                    action_type=hook_type,
                    target=target,
                    is_async=is_async,
                    payload=hook,
                    event_name=event_name,
                    **execution_kwargs
                )
            except Exception as e:
                import traceback
                print(f"[HooksManager] Error executing hook '{hook.get('name')}': {e}")
                traceback.print_exc()
                
hooks_manager = HooksManager()
