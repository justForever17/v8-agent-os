"""Engine-owned local owner, pairing and device credentials."""
from threading import RLock

from .owner import IdentityError, public_user

_service = None
_lock = RLock()


def get_identity_service():
    global _service
    with _lock:
        if _service is None:
            from core.v8_agent_os_paths import V8_AGENT_OS_HOME
            from core.security import credentials
            from .service import ClientIdentityService
            _service = ClientIdentityService(V8_AGENT_OS_HOME, credentials.credential_ref_store)
        return _service


__all__ = ["get_identity_service", "IdentityError", "public_user"]
