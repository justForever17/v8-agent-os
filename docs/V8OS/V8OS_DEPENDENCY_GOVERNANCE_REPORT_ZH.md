# V8 Agent OS 依赖治理报告

更新时间：2026-07-16
适用范围：Engine、Admin、Web、Phone、Shell、Desktop Pet、`product-ui`、`session-realtime`、Windows 原生辅助器

## 结论

本轮先清除了可以由源码事实证明未使用的依赖和残留，再应用同一兼容线内的安全更新。没有为了消除告警而跨越 Expo SDK、Electron、Vite、Tailwind、TypeScript 或 ESLint 主版本，也没有用危险的全局 `overrides` 强行压平原生或 Expo 依赖。

主要结果：

- Admin 不再携带早期“Admin 充当 Engine”遗留的 MCP SDK、AWS S3、Prisma adapter、Monaco 等假依赖。
- Web 不再携带无调用方的 AWS S3、slider、bcrypt、ANSI 转换等依赖。
- Engine 的最小/桌面预览依赖不再重复安装完整 `langchain`、`msgspec`、旧反爬指纹包和未使用的 `pyperclip`；补齐了源码实际直接使用的 `websockets`、`numpy` 与 Windows 音频依赖 `pycaw`。
- Phone 保持 Expo SDK 55，只升级官方 SDK 55 patch 矩阵，并把 React Native 固定在官方要求的 `0.83.6`。
- Admin/Web 的 React 更新至 `19.2.7`；嵌套 PostCSS 漏洞通过项目已有直接 PostCSS 版本统一解决。
- FlaUI Core/UIA2/UIA3 当前均为官方最新 `5.0.0`，不是待升级项。
- LangGraph 当前为 `1.2.9`、checkpoint 为 `4.1.1`，已高于已知反序列化漏洞修复线；但严格反序列化兼容验收尚未完成，不能直接把非公开的 `allowed_objects="core"` 写进生产配置。

## 1. 已清除的假依赖与残留

### Admin

已删除声明：

- `@modelcontextprotocol/sdk`：Admin 源码无调用方；MCP 真相在 Engine。
- `@aws-sdk/client-s3`、`@aws-sdk/lib-storage`、`@aws-sdk/s3-request-presigner`：唯一旧封装无调用方，连同 `src/lib/s3.ts` 删除。
- `@auth/prisma-adapter`：当前认证链未使用 Prisma adapter。
- `@monaco-editor/react`、`@radix-ui/react-avatar`、`caniuse-lite`、`cheerio`、`date-fns`、`framer-motion`、`gray-matter`、`react-syntax-highlighter`、直接 `zod`、`@types/bcryptjs`：源码无直接使用。

`@lobehub/icons-static-svg` 只供构建脚本复制图标，已移入 `devDependencies`。

同时清理了 11 个未纳入版本控制、已失效的 Prisma/LangChain/sidecar 管理脚本。保留并正式纳入版本控制的脚本只有当前 `package.json` 实际调用的 `sync-lobe-icons.mjs` 与 `validate-theme-coverage.mjs`。

### Web

已删除声明：

- 三个 AWS S3 SDK 包。
- `@radix-ui/react-slider` 及唯一未使用的 `slider.tsx`。
- `ansi-to-html`、`bcryptjs`、直接 `zod`、`@types/bcryptjs`。

`@types/three` 仅参与编译，移入 `devDependencies`。

### Phone

- 删除未使用的 `react-test-renderer`。
- 将 `expo-doctor` 作为显式开发依赖，避免每次诊断通过 `npx` 临时下载不可复现版本。

### Desktop Pet

- 删除无调用方的 `motion`、`autoprefixer` 和重复的 dev `vite` 声明。
- `@v8/session-realtime` 从过时的 `0.0.7` 对齐到当前共享契约 `0.0.16`。

### Engine

已删除声明：

- `python-dotenv`、`lark-oapi`：源码无直接导入。
- `curl_cffi`、`browserforge`、`apify-fingerprint-datapoints`、`msgspec`：最小产品线无调用方。
- `minimal.txt` / `desktop-preview.txt` 中重复的完整 `langchain` meta package；实际代码依赖保留为明确的 `langchain-core`、provider adapter、LangGraph 与 text splitters。
- `pyperclip`：产品路径无直接使用。

已补齐声明：

- `websockets>=15,<16`：Engine 有直接导入，15 是当前验证线。
- `numpy`：桌面媒体/图像链直接使用。
- `pycaw==20251023`：Windows computer-use 音频控制直接使用。
- `requirements/test.txt`：测试环境通过独立入口声明 `pytest`，不再把测试依赖混进生产包。

两处 `run_in_threadpool` 改为从 FastAPI 的公开入口导入，避免直接绑定 Starlette 内部路径。

## 2. 已应用的兼容升级

| 产品线 | 本轮结果 | 说明 |
| --- | --- | --- |
| Admin / Web | React、ReactDOM `19.2.7` | 同一 React 19 兼容线；生产构建通过 |
| Admin / Web | PostCSS 统一到直接依赖版本 | 消除 Next 嵌套旧 PostCSS 的已知漏洞；审计为 0 |
| Admin / Web | `eslint-plugin-react-hooks` 固定 `7.0.1` | `7.1.1` 在现有代码上新增大量规则误差/失败，暂不升级 |
| Phone | Expo SDK 55 全套 patch | 使用官方 SDK 55 对齐矩阵；Doctor 19/19 |
| Phone | React Native 精确 `0.83.6` | `^0.83.6` 会漂到 `0.83.10`，与 Expo 55 官方矩阵不符 |
| Desktop Pet | Tailwind 4 同线 patch、共享契约 `0.0.16` | Typecheck、构建通过 |
| Engine | FastAPI `0.139.1`、LangChain OpenAI `1.3.5`、Scrapling `0.4.11`、pycaw `20251023` | `pip check` 与定向 Engine 测试通过 |

当前关键 Engine 实装版本：

| 依赖 | 版本 |
| --- | --- |
| FastAPI / Starlette | `0.139.1` / `1.3.1` |
| Pydantic | `2.13.4` |
| LangGraph | `1.2.9` |
| LangGraph checkpoint / SQLite | `4.1.1` / `3.1.0` |
| LangChain / Core / OpenAI | `1.3.12` / `1.4.9` / `1.3.5` |
| ChromaDB | `1.5.9` |
| websockets | `15.0.1` |
| boto3 / botocore | `1.43.45` / `1.43.45` |

## 3. 已完成验证

- Admin：生产构建、i18n、主题覆盖检查通过；`npm audit` 为 0。
- Web：生产构建、typecheck、i18n 通过；`npm audit` 为 0。
- Phone：typecheck、i18n、Expo Doctor 19/19、Android export 通过；`npm audit` 为 0。
- Desktop Pet：typecheck、生产构建通过。
- Shell：28 项测试通过。
- `session-realtime`：22 项测试通过。
- Engine：`pip check`、Engine import、95 项 runtime/chat/context 定向测试通过。
- `v8os preview --rebuild`：Engine/Admin/Web/Shell 均真实启动；Engine health 返回 200，Admin/Web 根路由按认证设计返回 307；验收后全部停止。
- 所有 Node 工作区均执行了 `npm install --ignore-scripts` 和 `npm ls --depth=0`，锁文件与根声明一致。

仍存在但不应在本轮伪装成“已修复”的基线问题：

- Admin lint 仍有 6 errors / 13 warnings；Web lint 仍有 30 errors / 22 warnings，主要是旧 CommonJS 测试与既有 Hook 规则债。升级 hooks 插件到 7.1.1 会显著放大失败，因此保持 7.0.1。
- boto3/botocore patch 下载两次因网络中断失败；当前版本一致且 `pip check` 通过，本轮不继续重试。
- 本机只有 .NET runtime、没有 .NET SDK，无法运行 `dotnet list package`；官方 NuGet 已确认 FlaUI 三个包的 `5.0.0` 是最新版本。

## 4. 破坏性升级路线

### P0：LangGraph checkpoint 严格反序列化

当前版本已经高于 GHSA-g48c-2wqr-h844 的修复版本 `1.0.10`，但 V8OS 的 `AsyncSqliteSaver` 仍使用默认 serializer。建议先做兼容性专项，不直接采用传闻中的 `allowed_objects="core"`：当前 Python 公共 API 是 `allowed_msgpack_modules`，而 `allowed_objects` 只在部分内部 Reviver/发布记录中出现。

升级/加固步骤：

1. 对 checkpoint 数据库做在线备份和 quick-check。
2. 在 harness 中设置 `LANGGRAPH_STRICT_MSGPACK=true`。
3. 覆盖 Supervisor 自定义 state、LangChain messages、工具返回、interrupt/resume、审批等待、runtime handoff 和旧 checkpoint 恢复。
4. 优先使用图 state schema 派生的 allowlist；确有自定义类时才加入精确模块/类型元组。
5. 禁止 `pickle_fallback`；不兼容对象必须显式失败并进入可观测诊断。
6. 通过真实 resume 等价测试后再灰度启用；失败时恢复旧数据库与旧 serializer 配置。

### P0：Phone Expo 55 -> 56 -> 57

必须逐 SDK 升级，不能从 55 直接改依赖版本：

1. SDK 55 基线保留 Doctor、Android export、development build 真机 smoke。
2. 升 SDK 56，运行 `expo install --fix`、Doctor、CNG/prebuild diff、相机/音频/视频/SQLite/secure-store/Router 全链测试。
3. 处理 SDK 56 的 fetch、Router、iOS deployment target 等破坏性变化。
4. 再升 SDK 57，并同步 Node 最低版本、React Native 0.86、iOS 16.4+ 边界。
5. 每一步单独提交和回滚，不通过时停留在上一个 SDK。

即使未来 SDK 工具链再次出现间接审计告警，也应通过官方 SDK 迁移解决；禁止用跨 major `uuid` override 破坏 Expo CLI。

### P1：Admin/Web Tailwind 3 -> 4

Desktop Pet 已在 Tailwind 4，Admin/Web 仍为 Tailwind 3。迁移需要：

- CSS-first 配置与 `@tailwindcss/postcss`。
- 检查浏览器最低版本（Safari 16.4、Chrome 111、Firefox 128）。
- 全量核对边框、阴影、ring、颜色、容器和 dark mode 默认变化。
- 先迁共享 token/基础组件，再迁页面，最后跑主题覆盖与视觉回归。

### P1：TypeScript 5.9 -> 6 -> 7

- 先把所有工作区升级到 TypeScript 6，处理新默认值、废弃项和 `tsc file` 行为变化。
- TypeScript 7 是 Go 原生编译器，当前不提供完整 programmatic API；需要与 `@typescript/typescript6` 并存验证。
- 等 ESLint、Next/Expo、类型生成脚本和编辑器插件明确支持后再切主编译器。

### P1：ESLint 9 -> 10

前置条件是先清偿现有 lint 基线，并解决 hooks 7.1.1 新增失败。ESLint 10 还要求更高 Node 版本，并改变配置查找、recommended 规则和旧 eslintrc 支持，不能与页面功能修改混在同一提交。

### P1：Desktop Shell / Pet

按以下顺序拆分：

1. Vite 6 -> 7，修正 Node 最低版本和旧 API。
2. 先试 `rolldown-vite`，再升 Vite 8，验证 optimizeDeps、worker、asset 与 Electron renderer 构建。
3. Express 4 -> 5，重点检查 wildcard/path-to-regexp、dotfiles、Promise error 和 body parser 默认值。
4. Shell 与 Desktop Pet 的 Electron 39 -> 43 必须保持同一主版本，一次跨一个 major 验证 preload、IPC、tray、clipboard、PDF、通知和打包。
5. `@vitejs/plugin-react`、esbuild、TypeScript 等跟随 Vite 分阶段升级，避免一次更换整条工具链。

### P2：其他 major

- `websockets 15 -> 16`：Python 3.12 满足版本要求，但需真实验证代理、重连、超时和关闭握手。
- Express 5、Sharp 0.35、Three 0.185、Lucide 1.x、UUID 14：分别做小型兼容提交，不做全仓批量升级。
- NextAuth 5 当前仍为 beta；`npm outdated` 把稳定 4.x 当作“latest”不代表应降级。

## 5. 统一验收和回滚门禁

每条破坏性升级至少满足：

- 升级前保存锁文件、配置、关键 SQLite 数据和基线时延。
- `install/check -> unit -> integration -> production build -> preview/live smoke` 分层执行。
- 对有副作用的脚本先 dry-run；桌面/Phone/Engine 必须真实启动。
- 性能退化不得超过 10%，错误率增加不得超过 0.1%。
- 单独 scoped commit；验证 `git diff --check` 与 reverse-apply；失败只回滚该升级提交。
- 任何临时 shim 必须登记技术债、调用量和移除条件，不能永久保留。

## 6. 官方资料

- LangGraph 安全公告：<https://github.com/langchain-ai/langgraph/security/advisories/GHSA-g48c-2wqr-h844>
- LangGraph persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph releases：<https://github.com/langchain-ai/langgraph/releases>
- Expo SDK 版本矩阵：<https://docs.expo.dev/versions/latest/>
- Expo SDK 升级流程：<https://docs.expo.dev/workflow/upgrading-expo-sdk-walkthrough/>
- Expo SDK 56：<https://expo.dev/changelog/sdk-56>
- Tailwind 4 upgrade guide：<https://tailwindcss.com/docs/upgrade-guide>
- ESLint 10 migration：<https://eslint.org/docs/latest/use/migrate-to-10.0.0>
- Vite 7 migration：<https://v7.vite.dev/guide/migration>
- Vite 8 migration：<https://vite.dev/guide/migration.html>
- Electron breaking changes：<https://www.electronjs.org/docs/latest/breaking-changes>
- Express 5 migration：<https://expressjs.com/en/guide/migrating-5/>
- TypeScript 6：<https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/>
- TypeScript 7：<https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/>
- websockets changelog：<https://websockets.readthedocs.io/en/stable/project/changelog.html>
- PostCSS advisory：<https://github.com/advisories/GHSA-qx2v-qp2m-jg93>
- FlaUI Core：<https://www.nuget.org/packages/FlaUI.Core/>
- FlaUI UIA2：<https://www.nuget.org/packages/FlaUI.UIA2/>
- FlaUI UIA3：<https://www.nuget.org/packages/FlaUI.UIA3/>
