from typing import Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.storage import storage
from core.action_executor import ActionExecutor

class CronManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    def sync_jobs_to_scheduler(self):
        """Validate the whole plan before replacing any working schedule."""
        from copy import deepcopy

        current = {job.id: job for job in self.scheduler.get_jobs()}
        config = storage.get_cron_config()
        try:
            if not isinstance(config, dict) or not isinstance(config.get("jobs", []), list):
                raise ValueError("jobs must be a list")
            parsed = {}
            seen = set()
            for job_cfg in config.get("jobs", []):
                if not isinstance(job_cfg, dict) or not str(job_cfg.get("id") or "").strip():
                    raise ValueError("every job requires an id")
                job_id = str(job_cfg["id"]).strip()
                if job_id in seen:
                    raise ValueError(f"duplicate job id: {job_id}")
                seen.add(job_id)
                if not job_cfg.get("enabled", False):
                    continue
                trigger = CronTrigger.from_crontab(str(job_cfg.get("cron_expression") or ""))
                parsed[job_id] = (deepcopy(job_cfg), trigger)
        except (TypeError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc), "preserved": sorted(current)}

        scheduled = []
        errors = {}
        for job_id, (job_cfg, trigger) in parsed.items():
            try:
                options = {"trigger": trigger, "name": str(job_cfg.get("name") or job_id),
                           "kwargs": {"job_cfg": job_cfg}}
                if job_id in current:
                    # modify_job also updates pending jobs before scheduler.start;
                    # add_job(replace_existing=True) would leave duplicate pending IDs.
                    self.scheduler.modify_job(job_id, **options)
                else:
                    self.scheduler.add_job(self.execute_job, id=job_id, **options)
                scheduled.append(job_id)
            except Exception as exc:
                errors[job_id] = str(exc)
        for job_id in current.keys() - parsed.keys():
            try:
                self.scheduler.remove_job(job_id)
            except Exception as exc:
                errors[job_id] = str(exc)
        return {"status": "partial" if errors else "success", "scheduled": sorted(scheduled),
                "preserved": sorted(current.keys() & errors.keys()), "errors": errors}

    async def execute_job(self, job_cfg: Dict[str, Any]):
        """Callback to execute the actual job action using ActionExecutor."""
        print(f"[CronManager] Executing Cron Job: {job_cfg.get('name')}")
        action_type = job_cfg.get("action_type")
        target = job_cfg.get("action_target")
        payload = dict(job_cfg.get("payload", {}) or {})
        for key in (
            "triggerKind",
            "targetBinding",
            "recoveryAnchor",
            "attachPolicy",
            "wakeReason",
            "message",
            "sourceMetadata",
        ):
            if job_cfg.get(key) is not None:
                payload[key] = job_cfg.get(key)
        execute_kwargs: Dict[str, Any] = {
            "trigger": "cron",
            "cron_job_id": job_cfg["id"],
        }
        for key in (
            "session_id",
            "conversation_id",
            "parent_session_id",
            "user_id",
            "project_id",
            "workspace_id",
            "workspace_path",
            "resolved_scope",
            "scope_source",
            "scope_chain",
        ):
            if job_cfg.get(key) is not None:
                execute_kwargs[key] = job_cfg.get(key)
        
        try:
            # We assume cron typically uses an async wrapper 
            # since APScheduler provides AsyncIOScheduler bound to the EventLoop.
            ActionExecutor.execute(
                action_type=action_type,
                target=target,
                is_async=True, # Always spin job out as async task so scheduler isn't blocked 
                payload=payload,
                **execute_kwargs,
            )
        except Exception as e:
            print(f"[CronManager] Execution of job {job_cfg.get('id')} failed: {e}")

    def start(self):
        result = self.sync_jobs_to_scheduler()
        if self.scheduler.running:
            return result
        self.scheduler.start()
        print("[CronManager] Scheduler started.")
        return result
        
    def shutdown(self):
        if not self.scheduler.running:
            return {"status": "already_stopped"}
        self.scheduler.shutdown(wait=False)
        print("[CronManager] Scheduler shutdown.")
        return {"status": "stopped"}
        
cron_manager = CronManager()
