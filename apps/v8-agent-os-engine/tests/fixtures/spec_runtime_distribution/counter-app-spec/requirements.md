# Requirements

## REQ-001 可复现交付

WHEN 工程运行时执行本 Spec THEN 系统 SHALL 生成一个浏览器计数器页面，并在页面源代码中包含 `SPEC_DRY_RUN_COUNTER` 标记。

## REQ-002 交互行为

WHEN 用户点击计数按钮 THEN 页面 SHALL 将当前计数增加 1，并保持中文按钮文案可读。

## REQ-003 文档说明

WHEN 交付完成 THEN 系统 SHALL 提供简短 README，说明如何打开 `index.html` 和验证按钮行为。
