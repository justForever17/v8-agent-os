# Design

## DES-001 技术框架

本 dry-run fixture 使用自包含 `index.html`，通过原生 HTML、CSS 和 inline JavaScript 实现；不引入构建工具、框架脚手架或外部依赖。

## DES-002 页面结构

`index.html` 包含标题、计数显示、按钮和 `SPEC_DRY_RUN_COUNTER` 注释标记。按钮点击时通过 inline JavaScript 更新计数文本。

## DES-003 验证策略

验证只需要读取 `index.html` 和 README：确认标记存在、按钮文案为中文、README 说明了打开方式和手动 smoke test。
