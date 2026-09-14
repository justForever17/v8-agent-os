import threading
from collections import OrderedDict

from core.storage import storage
from core.action_executor import ActionExecutor
from erc.runtime_context import bind_runtime_context, get_runtime_context


class HooksManager:
    """Dispatch lifecycle hooks with scope and causal recursion checks."""

    _dedupe_lock = threading.RLock()
    # Bounded process-local delivery cache; execution receipts remain the durable
    # side-effect owner. Events without an occurrence identity are not guessed.
    _recent_dispatches = OrderedDict()
    _dedupe_capacity = 4096

    @staticmethod
    def _hook_identity(hook):
        return str(hook.get("id") or repr((hook.get("name"), hook.get("type"), hook.get("target"))))

    @classmethod
    def _dispatch_key(cls, event_name, hook, kwargs, context):
        causal_id = (kwargs.get("occurrence_id") or kwargs.get("event_id")
                     or kwargs.get("tool_call_id") or kwargs.get("model_run_id"))
        if not causal_id and event_name in {"on_chat_end", "on_supervisor_start", "on_supervisor_end"}:
            causal_id = kwargs.get("run_id") or kwargs.get("parent_run_id") or context.get("run_id")
        if not causal_id:
            return None
        scope = tuple(str(kwargs.get(key) or context.get(key) or "") for key in
                      ("user_id", "project_id", "workspace_id"))
        source_session = kwargs.get("session_id") or kwargs.get("parent_session_id") or context.get("session_id")
        return (event_name, cls._hook_identity(hook), *scope, str(source_session or ""), str(causal_id))

    @classmethod
    def _reserve_dispatch(cls, key):
        if key is None:
            return True
        with cls._dedupe_lock:
            if key in cls._recent_dispatches:
                return False
            cls._recent_dispatches[key] = None
            while len(cls._recent_dispatches) > cls._dedupe_capacity:
                cls._recent_dispatches.popitem(last=False)
            return True

    def execute_hook(self, event_name: str, **kwargs):
        config = storage.get_hooks_config()
        hooks = config.get("hooks", [])
        exclude_targets = {str(item).strip() for item in (kwargs.pop("exclude_targets", None) or []) if str(item).strip()}
        exclude_names = {str(item).strip() for item in (kwargs.pop("exclude_names", None) or []) if str(item).strip()}
        runtime_context = get_runtime_context()

        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            if not hook.get("enabled", False) or str(hook.get("status") or "").lower() in {"paused", "deleted", "disabled"}:
                continue
            target = hook.get("target")
            if not target or str(target).strip() in exclude_targets:
                continue
            if str(hook.get("name") or "").strip() in exclude_names:
                continue
            hook_events = hook.get("events", [])
            if isinstance(hook_events, str):
                hook_events = [hook_events]
            if event_name not in hook_events and "*" not in hook_events:
                continue

            scope_mismatch = False
            for key in ("user_id", "project_id", "workspace_id", "session_id"):
                actual = kwargs.get(key) or runtime_context.get(key)
                if key == "session_id":
                    actual = kwargs.get(key) or kwargs.get("parent_session_id") or actual
                if hook.get(key) is not None and str(hook[key]).strip() != str(actual or "").strip():
                    scope_mismatch = True
                    break
            if scope_mismatch:
                continue

            identity = self._hook_identity(hook)
            chain = list(runtime_context.get("hook_chain") or [])
            if identity in chain:
                continue
            if str(kwargs.get("trigger") or "").startswith("hook:") and (
                (hook.get("name") and kwargs.get("hook_name") == hook["name"])
                or kwargs.get("hook_target") == target
            ):
                continue
            key = self._dispatch_key(event_name, hook, kwargs, runtime_context)
            if not self._reserve_dispatch(key):
                continue

            try:
                execution_kwargs = dict(kwargs)
                for scope_key in (
                    "user_id", "project_id", "workspace_id", "workspace_path",
                    "resolved_scope", "scope_source", "scope_chain",
                ):
                    if execution_kwargs.get(scope_key) is None and runtime_context.get(scope_key) is not None:
                        execution_kwargs[scope_key] = runtime_context[scope_key]
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
                is_async = hook.get("async", False)
                if is_async and execution_kwargs.get("session_id"):
                    execution_kwargs.setdefault("parent_session_id", execution_kwargs["session_id"])
                    execution_kwargs.pop("session_id", None)
                with bind_runtime_context(hook_chain=[*chain, identity]):
                    ActionExecutor.execute(
                        action_type=hook.get("type", "command"),
                        target=target,
                        is_async=is_async,
                        payload=hook,
                        event_name=event_name,
                        **execution_kwargs,
                    )
            except Exception as exc:
                # Dispatch did not get accepted; do not suppress a later retry.
                with self._dedupe_lock:
                    self._recent_dispatches.pop(key, None)
                print(f"[HooksManager] Error executing hook '{hook.get('name')}': {exc}")


hooks_manager = HooksManager()
