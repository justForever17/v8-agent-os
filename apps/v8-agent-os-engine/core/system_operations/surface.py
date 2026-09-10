"""Agent projection; execution and credential truth stay with their owners."""


def render_system_operation(payload: dict, raw_ref: str) -> str:
    lines = ["受控系统操作"]
    platform = payload.get("platform")
    if isinstance(platform, dict):
        lines.append(f"平台：{platform.get('os', 'unknown')}")
        for key, label in (("privilege", "提权服务"), ("unlock", "解锁组件")):
            status = platform.get(key)
            if not isinstance(status, dict):
                continue
            lines.append(f"{label}：已注册={status.get('registered', False)}；当前可用={status.get('available', False)}")
            if key == "unlock":
                lines.append(f"屏幕锁定：{status.get('locked', '未知')}")
        for key, configured in (payload.get("credentials") or {}).items():
            lines.append(f"{'解锁' if key == 'unlock' else '提权'}凭据：{'已配置' if configured else '未配置'}")
    else:
        lines.append(f"结果：{'已完成' if payload.get('ok') is True else '未完成'}；系统核验={'通过' if payload.get('verified') is True else '未确认'}")
        for key, label in (("summary", "说明"), ("code", "状态"), ("exitCode", "退出码"), ("elapsedMs", "耗时毫秒")):
            if payload.get(key) is not None:
                lines.append(f"{label}：{payload[key]}")
        for key, label in (("stdout", "标准输出"), ("stderr", "错误输出")):
            if payload.get(key):
                lines.extend([label + "：", str(payload[key])])
        truncation = payload.get("outputTruncated")
        if (any(truncation.values()) if isinstance(truncation, dict) else truncation):
            lines.append("输出超过平台保留范围，部分内容未保留；不能据此断言完整输出。")
    if raw_ref:
        lines.append(f"完整记录：tool_observation_detail(raw_ref='{raw_ref}')")
    return "\n".join(lines)
