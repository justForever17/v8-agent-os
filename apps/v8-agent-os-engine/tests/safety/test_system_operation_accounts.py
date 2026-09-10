import sys
import uuid

import pytest

from core.system_operations.accounts import current_account, validate_account
from core.system_operations.service import SystemOperationError


def test_current_account_identity_does_not_require_password():
    account = current_account()
    username, _ = validate_account("unlock", **account)
    assert username.casefold() == account["username"].casefold()


def test_nonexistent_account_rejected_before_authentication():
    with pytest.raises(SystemOperationError):
        validate_account("run_privileged", "v8-nonexistent-" + uuid.uuid4().hex, "")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows local account contract")
def test_remote_domain_cannot_trigger_outbound_name_resolution(monkeypatch):
    import win32net
    monkeypatch.setattr(win32net, "NetUserGetInfo", lambda *a: pytest.fail("queried an unapproved domain"))
    with pytest.raises(SystemOperationError):
        validate_account("unlock", "account", "remote.invalid")
