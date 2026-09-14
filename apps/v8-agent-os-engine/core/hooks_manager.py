from copy import deepcopy
from uuid import uuid4

from core.storage import storage
from core.automation.definitions import fingerprint
from core.automation.delivery import automation_delivery_service
from erc.runtime_context import get_runtime_context


class HooksManager:
    """Dispatch lifecycle hooks with scope and causal recursion checks."""

    @staticmethod
    def _hook_identity(hook):
        return str(hook.get("id") or repr((hook.get("name"), hook.get("type"), hook.get("target"))))

    def execute_hook(self, event_name: str, **kwargs):
        config = storage.get_hooks_config()
        hooks = config.get("hooks", [])
        exclude_targets = {str(item).strip() for item in (kwargs.pop("exclude_targets", None) or []) if str(item).strip()}
        exclude_names = {str(item).strip() for item in (kwargs.pop("exclude_names", None) or []) if str(item).strip()}
        runtime_context = get_runtime_context()
        entries = []
        source_session = kwargs.get("session_id") or kwargs.get("parent_session_id") or runtime_context.get("session_id") or "automation:hooks"
        source_run = kwargs.get("parent_run_id") or kwargs.get("run_id") or runtime_context.get("run_id")

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
            execution_kwargs.setdefault("event_name", event_name)
            execution_kwargs.setdefault("task_name", hook.get("name") or f"hook:{event_name}")
            execution_kwargs.setdefault("hook_name", hook.get("name"))
            execution_kwargs.setdefault("hook_target", target)
            is_async = hook.get("async", False)
            if is_async and execution_kwargs.get("session_id"):
                execution_kwargs.setdefault("parent_session_id", execution_kwargs["session_id"])
                execution_kwargs.pop("session_id", None)
            execution_kwargs["hook_chain"] = [*chain, identity]
            if source_run:
                execution_kwargs.setdefault("parent_run_id", source_run)
            definition = deepcopy(hook)
            definition.setdefault("id", identity)
            definition.setdefault("definitionRevision", fingerprint(hook))
            entries.append({
                "definition": definition, "action_type": hook.get("type", "command"),
                "target": target, "is_async": is_async, "payload": hook, "kwargs": execution_kwargs,
            })

        # Freeze the whole matching fan-out with the source receipt. A replay of
        # this source never reselects newer definitions or creates another run.
        causal = (kwargs.get("occurrence_id") or kwargs.get("event_id")
                  or kwargs.get("tool_call_id") or kwargs.get("model_run_id"))
        if not causal and event_name in {"on_chat_end", "on_supervisor_start", "on_supervisor_end"}:
            causal = source_run
        scope = [kwargs.get(key) or runtime_context.get(key) for key in ("user_id", "project_id", "workspace_id")]
        source_id = ("terminal-hook:" + str(source_run) if event_name == "on_chat_end" and source_run
                     else "hook-source-" + fingerprint([event_name, source_session, scope, causal or uuid4().hex]))
        deliveries = automation_delivery_service.enqueue(
            kind="hook", source_event_id=source_id, source_session_id=source_session, source_run_id=source_run,
            event_payload={"eventName": event_name, "occurrence": causal, "scope": scope}, entries=entries,
        )
        for delivery in deliveries:
            if not delivery["envelope"]["is_async"]:
                automation_delivery_service.execute_inline(delivery)
        automation_delivery_service.kick()
        return deliveries



hooks_manager = HooksManager()
