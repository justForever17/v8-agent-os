from scripts.runtime.cron_supervisor_task import run


__all__ = ["run"]


if __name__ == "__main__":
    run(payload={"task": "请汇报当前系统状态"})
