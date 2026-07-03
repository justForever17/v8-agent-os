from typing import Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.storage import storage
from core.action_executor import ActionExecutor

class CronManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._init_jobs_from_config()

    def _init_jobs_from_config(self):
        """Loads jobs from configuration on startup."""
        return None

    def sync_jobs_to_scheduler(self):
        """Syncs all enabled jobs to the APScheduler."""
        # Remove all existing user jobs
        for job in self.scheduler.get_jobs():
            self.scheduler.remove_job(job.id)
            
        config = storage.get_cron_config()
        for job_cfg in config.get("jobs", []):
            if not job_cfg.get("enabled", False):
                continue
                
            cron_expr = job_cfg.get("cron_expression")
            if not cron_expr:
                continue
                
            try:
                # cron expressions: minute hour day month day_of_week
                # simple split by space, if 5 parts
                parts = cron_expr.strip().split()
                if len(parts) != 5:
                    print(f"[CronManager] Invalid cron expression for {job_cfg.get('name')}: {cron_expr}")
                    continue
                    
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4]
                )
                
                self.scheduler.add_job(
                    self.execute_job,
                    trigger=trigger,
                    id=job_cfg["id"],
                    name=job_cfg["name"],
                    kwargs={"job_cfg": job_cfg},
                    replace_existing=True
                )
            except Exception as e:
                print(f"[CronManager] Error parsing cron config for '{job_cfg.get('name')}': {e}")

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
        self.sync_jobs_to_scheduler()
        self.scheduler.start()
        print("[CronManager] Scheduler started.")
        
    def shutdown(self):
        self.scheduler.shutdown()
        print("[CronManager] Scheduler shutdown.")
        
cron_manager = CronManager()
