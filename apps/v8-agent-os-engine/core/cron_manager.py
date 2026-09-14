from copy import deepcopy
from typing import Dict, Any
from uuid import uuid4

from apscheduler.events import EVENT_JOB_SUBMITTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger

from core.automation.definitions import fingerprint
from core.automation.delivery import automation_delivery_service
from core.storage import storage


class CronManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_listener(self._on_submission, EVENT_JOB_SUBMITTED)

    @staticmethod
    def _parse(jobs):
        if not isinstance(jobs, list):
            raise ValueError("jobs must be a list")
        parsed, seen = {}, set()
        for value in jobs:
            if not isinstance(value, dict) or not str(value.get("id") or "").strip():
                raise ValueError("every job requires an id")
            job_id = str(value["id"])
            if job_id.startswith("_automation_") or job_id in seen:
                raise ValueError(f"duplicate or reserved job id: {job_id}")
            seen.add(job_id)
            if value.get("enabled", False):
                parsed[job_id] = (deepcopy(value), CronTrigger.from_crontab(str(value.get("cron_expression") or "")))
        return parsed

    def _installed_jobs(self):
        return {job.id: job for job in self.scheduler.get_jobs() if not job.id.startswith("_automation_")}

    def sync_jobs_to_scheduler(self):
        current = self._installed_jobs()
        config = storage.get_cron_config()
        recovering, reason = False, None
        try:
            if not isinstance(config, dict):
                raise ValueError("cron config must be an object")
            parsed = self._parse(config.get("jobs", []))
        except (TypeError, ValueError) as exc:
            reason = str(exc)
            if current or not isinstance(config, dict) or not config.get("appliedPlan"):
                return {"status": "rejected", "reason": reason, "preserved": sorted(current)}
            parsed = self._parse(config["appliedPlan"]["jobs"])
            recovering = True
        pause_for_publication = self.scheduler.state == STATE_RUNNING
        if pause_for_publication:
            self.scheduler.pause()
        try:
            return self._install_plan(parsed, current, config, recovering, reason)
        finally:
            if pause_for_publication:
                self.scheduler.resume()

    def _install_plan(self, parsed, current, config, recovering, reason):
        preimage = {job_id: {"trigger": job.trigger, "name": job.name, "kwargs": deepcopy(job.kwargs)}
                    for job_id, job in current.items()}
        errors, scheduled = {}, []
        for job_id, (job_cfg, trigger) in parsed.items():
            try:
                options = {"trigger": trigger, "name": str(job_cfg.get("name") or job_id), "kwargs": {"job_cfg": job_cfg}}
                if job_id in current:
                    self.scheduler.modify_job(job_id, **options)
                else:
                    self.scheduler.add_job(self._scheduled_wakeup, id=job_id, **options)
                scheduled.append(job_id)
            except Exception as exc:
                errors[job_id] = str(exc)
        for job_id in current.keys() - parsed.keys():
            try:
                self.scheduler.remove_job(job_id)
            except Exception as exc:
                errors[job_id] = str(exc)
        if not recovering:
            installed = [deepcopy(job.kwargs["job_cfg"]) for job in self._installed_jobs().values()]
            try:
                if not storage.publish_applied_cron_plan(expected_jobs=config.get("jobs", []), applied_jobs=installed):
                    raise RuntimeError("definition changed while publishing the applied plan")
            except Exception as exc:
                errors["publication"] = str(exc)
                for job_id in self._installed_jobs().keys() - preimage.keys():
                    self.scheduler.remove_job(job_id)
                for job_id, options in preimage.items():
                    if self.scheduler.get_job(job_id):
                        self.scheduler.modify_job(job_id, **options)
                    else:
                        self.scheduler.add_job(self._scheduled_wakeup, id=job_id, **options)
                scheduled = []
        return {"status": "partial" if errors else "recovered" if recovering else "success",
                "scheduled": sorted(scheduled), "preserved": sorted(current.keys() & errors.keys()),
                "errors": errors, "reason": reason}

    def _on_submission(self, event):
        if event.job_id.startswith("_automation_"):
            return
        job = self.scheduler.get_job(event.job_id)
        if not job:
            return
        for scheduled_time in event.scheduled_run_times:
            # The scheduler supplies the scheduled instant, including its
            # coalescing/misfire policy. Callback wall-clock time is not an ID.
            self._enqueue(job.kwargs["job_cfg"], occurrence=scheduled_time.isoformat())

    async def _scheduled_wakeup(self, job_cfg):
        # The submission listener commits before this coroutine gets a turn.
        automation_delivery_service.kick()

    @staticmethod
    def _enqueue(job_cfg, *, occurrence, manual=False):
        from runtimes.automation.runtime import automation_runtime

        definition = deepcopy(job_cfg)
        definition.setdefault("definitionRevision", fingerprint(job_cfg))
        payload = dict(job_cfg.get("payload", {}) or {})
        for key in ("triggerKind", "targetBinding", "recoveryAnchor", "attachPolicy", "wakeReason", "message", "sourceMetadata"):
            if job_cfg.get(key) is not None:
                payload[key] = job_cfg[key]
        kwargs = {"trigger": "cron", "cron_job_id": job_cfg["id"], "task_name": job_cfg.get("name") or job_cfg["id"]}
        if manual:
            kwargs["automation_manual_trigger"] = True
            kwargs["automation_allow_disabled"] = not bool(job_cfg.get("enabled", False))
        for key in ("session_id", "conversation_id", "parent_session_id", "user_id", "project_id", "workspace_id",
                    "workspace_path", "resolved_scope", "scope_source", "scope_chain"):
            if job_cfg.get(key) is not None:
                kwargs[key] = job_cfg[key]
        source_session = kwargs.get("session_id") or automation_runtime.resolve_session_id(
            action_type=job_cfg["action_type"], target=job_cfg["action_target"], trigger_source="cron", kwargs=kwargs)
        source_id = "cron-source-" + fingerprint([job_cfg["id"], definition["definitionRevision"], occurrence])
        return automation_delivery_service.enqueue(
            kind="cron", source_event_id=source_id, source_session_id=source_session, source_run_id=None,
            event_payload={"kind": "cron", "jobId": job_cfg["id"], "occurrence": occurrence,
                           "definitionRevision": definition["definitionRevision"]},
            entries=[{"definition": definition, "action_type": job_cfg["action_type"], "target": job_cfg["action_target"],
                      "payload": payload, "is_async": True, "kwargs": kwargs}],
        )

    async def execute_job(self, job_cfg: Dict[str, Any], *, occurrence_id=None):
        deliveries = self._enqueue(job_cfg, occurrence=occurrence_id or f"manual:{uuid4().hex}", manual=True)
        automation_delivery_service.kick()
        return {"status": "queued", "deliveries": deliveries}

    def start(self):
        result = self.sync_jobs_to_scheduler()
        if self.scheduler.running:
            return result
        automation_delivery_service.start(self.scheduler)
        self.scheduler.start()
        return result

    def shutdown(self):
        if not self.scheduler.running:
            return {"status": "already_stopped"}
        self.scheduler.shutdown(wait=False)
        return {"status": "stopped"}


cron_manager = CronManager()
