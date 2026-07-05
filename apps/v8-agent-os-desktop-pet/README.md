# V8OS Desktop Pet

V8OS Desktop Pet 是 V8OS 的本地桌宠入口。它只负责语音输入、事件监听、动作展示和语音播报，不维护第二套聊天、模型、记忆或工具主链。

## 能力边界

- 通过本机 Admin BFF 获取 V8OS 会话能力。
- 读取项目和最近会话，支持托盘菜单快速切换监听目标。
- 发送语音消息、上传文件，并消费 V8OS realtime 事件。
- 将主理人回复、工具调用、运行状态、子代理协作、产物和审批事件映射为桌宠动作。
- 播放 V8OS 生成的语音内容。
- 提供透明置顶、点击穿透、静音、打开 V8OS、打开桌宠设置、关闭桌宠等本地菜单。

桌宠不直接调用 AI 模型，不写记忆，不执行工具，不创建运行时任务。完整执行真相仍在 V8OS Engine/Admin。

## 运行方式

安装依赖：

```powershell
npm install
```

如果 Electron 二进制下载被网络中断，可以先跳过二进制下载完成前端验证：

```powershell
$env:ELECTRON_SKIP_BINARY_DOWNLOAD='1'; npm install --ignore-scripts
```

开发代理：

```powershell
npm run dev
```

构建前端与本地代理：

```powershell
npm run build
```

以 Electron 加载已构建的 `dist/`：

```powershell
npm run desktop:dev
```

连接目标默认是本机 V8OS：

```powershell
V8_ADMIN_BASE_URL=http://127.0.0.1:9528
```

## 配置入口

桌宠自身不再提供重复的聊天、模型或连接配置。桌宠相关设置由 Admin 的“桌宠设置”页面负责：

- V8OS Event Voice
- Action Table
- 特效光谱

## 当前限制

- Electron 二进制需要网络可用或配置镜像后下载；前端和服务端构建不依赖 Electron binary。
- 第一版只做轻量桌面动画摘要，不展开 Phone/Web 的完整卡片栈。
- 语音输入和播报能力依赖 V8OS Admin BFF 已配置的音频链路。
