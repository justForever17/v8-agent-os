from typing import Optional, Dict, Any, List
from core.database import db

class AuditLogger:
    """
    Minimalist internal logger for recording background automation activities
    (like Hooks, Cron tasks, and Memory actions) without incurring LLM overhead.
    """
    
    @staticmethod
    def log(source_type: str, action: str, status: str, details: Optional[str] = None):
        """
        Logs an event to the system_audit_log table.
        Args:
            source_type: 'HOOK' | 'CRON' | 'SYSTEM'
            action: Brief description of the task (e.g. 'Extract Session Log')
            status: 'SUCCESS' | 'ERROR' | 'INFO' | 'WARNING'
            details: Optional short contextual info (e.g. session_id or error message)
        """
        # Execute synchronously as SQLite WAL mode is very fast,
        # but expose as async for future-proofing and consistency.
        db.add_audit_log(
            source_type=source_type,
            action=action,
            status=status,
            details=details
        )
        
    @staticmethod
    def get_logs(limit: int = 100, offset: int = 0, source_type: str = None, status: str = None) -> List[Dict[str, Any]]:
        """Retrieve audit logs."""
        return db.get_audit_logs(limit, offset, source_type, status)

audit_logger = AuditLogger()
