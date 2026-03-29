import json
import uuid
from typing import Dict, Any, List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from core.storage import storage
from core.action_executor import ActionExecutor
from core.database import db

class CronManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._init_jobs_from_config()

    def _init_jobs_from_config(self):
        """Loads jobs from configuration on startup."""
        config = storage.get_cron_config()
        jobs = config.get("jobs", [])
        
        # Inject standard Nightly Batch testing job if config is perfectly empty
        if len(jobs) == 0:
            default_job = {
                "id": str(uuid.uuid4()),
                "name": "Memory Nightly Batch",
                "cron_expression": "0 3 * * *", # 3 AM daily by default
                "action_type": "python",
                "action_target": "scripts.cron_nightly_memory_batch",
                "payload": {},
                "enabled": True
            }
            jobs.append(default_job)
            storage.save_cron_config({"jobs": jobs})

    def _heartbeat_job_config(self) -> Dict[str, Any] | None:
        runtime_config = storage.get_automation_runtime_config() or {}
        heartbeat = dict(runtime_config.get("supervisorHeartbeat") or {})
        if not bool(heartbeat.get("enabled", False)):
            return None

        interval_minutes = max(int(heartbeat.get("intervalMinutes") or 30), 5)
        message_template = str(
            heartbeat.get("messageTemplate")
            or "What did you do today? How is the task going? Why are you not continuing right now?"
        ).strip()
        return {
            "id": "system_supervisor_heartbeat",
            "name": "Supervisor Heartbeat",
            "system_job": True,
            "action_type": "agent",
            "action_target": "supervisor",
            "interval_minutes": interval_minutes,
            "payload": {
                "task": message_template,
                "kind": "supervisor_heartbeat",
                "onlyWhenIdle": bool(heartbeat.get("onlyWhenIdle", True)),
                "suppressWhenActiveRun": bool(heartbeat.get("suppressWhenActiveRun", True)),
            },
            "enabled": True,
        }

    def _should_skip_heartbeat(self, job_cfg: Dict[str, Any]) -> bool:
        payload = dict(job_cfg.get("payload") or {})
        only_when_idle = bool(payload.get("onlyWhenIdle", True))
        suppress_when_active = bool(payload.get("suppressWhenActiveRun", True))
        if not only_when_idle and not suppress_when_active:
            return False

        active_runs = db.list_active_run_records()
        if not active_runs:
            return False

        if suppress_when_active:
            return True
        if only_when_idle:
            return True
        return False

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

        heartbeat_job = self._heartbeat_job_config()
        if heartbeat_job:
            try:
                self.scheduler.add_job(
                    self.execute_job,
                    trigger=IntervalTrigger(minutes=int(heartbeat_job["interval_minutes"])),
                    id=heartbeat_job["id"],
                    name=heartbeat_job["name"],
                    kwargs={"job_cfg": heartbeat_job},
                    replace_existing=True,
                )
            except Exception as e:
                print(f"[CronManager] Error scheduling supervisor heartbeat: {e}")
                
    async def execute_job(self, job_cfg: Dict[str, Any]):
        """Callback to execute the actual job action using ActionExecutor."""
        print(f"[CronManager] Executing Cron Job: {job_cfg.get('name')}")
        action_type = job_cfg.get("action_type")
        target = job_cfg.get("action_target")
        payload = job_cfg.get("payload", {})

        if bool(job_cfg.get("system_job")) and str(payload.get("kind") or "").strip() == "supervisor_heartbeat":
            if self._should_skip_heartbeat(job_cfg):
                print("[CronManager] Skipping Supervisor Heartbeat because the system is not idle.")
                return
        
        try:
            # We assume cron typically uses an async wrapper 
            # since APScheduler provides AsyncIOScheduler bound to the EventLoop.
            ActionExecutor.execute(
                action_type=action_type,
                target=target,
                is_async=True, # Always spin job out as async task so scheduler isn't blocked 
                payload=payload,
                trigger="cron",
                cron_job_id=job_cfg["id"]
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
