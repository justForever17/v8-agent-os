"""OS account identity checks, without a password or authentication attempt."""
import sys

from .service import SystemOperationError


def current_account() -> dict:
    if sys.platform == "win32":
        import win32api
        return {"username": win32api.GetUserName(), "domain": "."}
    import os
    import pwd
    return {"username": pwd.getpwuid(os.getuid()).pw_name, "domain": ""}


def validate_account(action: str, username: str, domain: str) -> tuple[str, str]:
    username, domain = username.strip(), domain.strip()
    if sys.platform == "win32":
        import win32api
        import win32net
        computer = win32api.GetComputerName()
        if "\\" in username:
            prefix, username = username.split("\\", 1)
            if domain and domain.casefold() != prefix.casefold():
                raise SystemOperationError("system_account_invalid", "系统账户与域不一致。", 422)
            domain = prefix
        if domain.casefold() not in {"", ".", computer.casefold()} or any(c in username for c in "\\/@:"):
            raise SystemOperationError("system_account_invalid", "请输入这台电脑上的本地系统账户。", 422)
        try:
            account = win32net.NetUserGetInfo(None, username, 0)
        except Exception:
            raise SystemOperationError("system_account_not_found", "Windows 未找到此本地账户；这里不是 V8 控制台用户名。", 422) from None
        username = account["name"]
        if action == "unlock" and username.casefold() != win32api.GetUserName().casefold():
            raise SystemOperationError("unlock_current_account_required", "屏幕解锁必须配置当前 Windows 登录账户。", 422)
        return username, "."
    account = current_account()
    if domain or username != account["username"]:
        raise SystemOperationError("system_account_invalid", "请配置当前系统账户；sudo 不切换登录账户。", 422)
    return username, ""
