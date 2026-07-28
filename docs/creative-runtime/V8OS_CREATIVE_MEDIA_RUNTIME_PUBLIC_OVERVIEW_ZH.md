# V8OS Creative Media Runtime：对话式多媒体创作运行时

V8OS Creative Media Runtime 面向图片、视频、语音、音乐和 3D 媒体创作。它把用户目标编译成可追踪的 recipe、素材引用、provider job、编辑动作、质量证据和最终产物，而不是把“生成一张图”或“做一段视频”压成一次不可检查的 API 调用。

模型和能力以 Model Hub 中用户实际保存的 provider endpoint、原生模型 ID 与 capability 为真相。内置供应商和模型 JSON 只帮助填写，不能覆盖真实配置，也不能用“V8OS 已适配”白名单过滤用户明确声明的多媒体能力。

## Agent 工具面

Creative Media 对 Agent 只暴露六个稳定 facade：

```text
creative_media_capabilities
creative_media_plan
creative_media_assets
creative_media_jobs
creative_media_edit
creative_media_quality
```

每个 facade 的 action、必填字段、允许字段、副作用和返回类型来自同一 action registry。未知 action 或多余字段返回结构化错误；provider 原始 payload 留在 Runtime Surface，Agent Surface 只接收摘要、证据、阻塞和下一步。普通任务不会常驻 provider matrix、模型列表或整套媒体参数。

## 创作链路

1. **Recipe 与提示词**
   将口语目标编译为 provider-neutral recipe，再转换为具体 provider 请求。用户的硬需求、负向约束、原文字幕、品牌文字和参考关系必须保留。

2. **素材与血缘**
   区分用户 source、Agent artifact、工作区素材和内部编辑资源。用户上传不会重复登记为 Agent 产物；工作区已有文件也不会因为被扫描到就自动成为产物。

3. **角色与跨镜头一致性**
   通过角色设定、参考图/视频、首尾帧、桥接帧、镜头 recipe 和 artifact refs 维持多轮生成的一致性，不在每一镜重新猜角色。

4. **Provider Job**
   按 `modality + operationKind` 区分图片生成/编辑、文生视频、图生视频、首尾帧、主体参考、对口型、动作迁移、语音、音乐和 3D。fallback 只能在兼容的 operation kind 与输入合同之间发生，不能用相似显示名偷换能力。

5. **质量与交付**
   记录文件可打开性、比例、分辨率、时长、帧/采样边界、编码、音频流、跨镜头一致性、成本和安全改写证据。失败、降级或缺少 provider 能力会明确返回阻塞，不伪造成功产物。

## Creative Artifact Canvas

Web 端提供 Creative Artifact Canvas，作为创意产物的全尺寸工作台，而不是塞进聊天侧栏的受限浏览器。它支持：

- 从当前会话产物和工作区媒体素材库添加图片、视频与音频；
- 连接素材、框选、移动、播放和查看媒体信息；
- 创建蒙版并发起局部图片编辑；
- 从画布动作沿正常 ChatRuntime 唤醒当前会话的 Supervisor；
- 在运行期间锁定会破坏 source/artifact/mask lineage 的自由修改。

可复用素材归工作区，当前会话必须显式采用后才能参与本轮创作；跨工作区引用会被拒绝。蒙版属于内部编辑输入，不进入普通素材库。画布不会创建独立的隐藏会话、插件授权或旁路执行状态。Phone 目前只消费正常消息、来源和产物，不提供 Web 的完整画布编辑面。

## 精确本机媒体编辑

精确抽帧、视频分段和音频分段由 Engine 自有的 governed media 路径执行，不经过云端 provider，也不依赖 MediaKit 插件授权：

- FFmpeg 与 FFprobe 必须来自同一套可用安装，版本均为 7.0 或更高；
- 执行前固定输入 probe fingerprint；
- 视频按 frame index、PTS 和 time base 验证边界；
- 音频按 sample index 与 sample rate 验证边界；
- 输出重新探测并核对目标帧数、采样数或时间范围。

这条路径解决确定性剪切和抽取。云端生成、ASR、OCR、增强、复杂剪辑和供应商专属能力仍按其 provider 或插件合同执行，两者不能互相冒充。

## Plugin 与 Extensions 的关系

Creative Media 是媒体计划、job、artifact 与 QA 的权威 runtime。Extensions 可以提供普通媒体 Skill/MCP 候选；Plugin Manager 则可以在有效 task grant 下投影已安装插件的精确组件包。

火山引擎 MediaKit CLI 提供更广的本地/云端媒体动作，并同步当前安装版本的完整命令 schema；它不会替代 Engine 的精确 frame/sample 编辑路径，也不会因为“已安装”自动获得调用权。Skill 正文继续由通用 `fetch_skill_instructions` 按需读取，不把完整资源包常驻进 Agent content。

## ComfyUI 当前边界

Admin 可以通过 `GET /object_info` 探测 ComfyUI provider，并为模型记录 workflow capability。当前尚未完成通用 workflow 模板注册、参数映射和执行适配，因此“provider 可达”不等于 V8OS 已能自动上传或执行任意 ComfyUI workflow JSON。

## 当前状态与限制

当前已具备六 facade 工具面、recipe、素材/产物 ledger、provider job、角色和关键帧引用、Web Creative Artifact Canvas、工作区素材库、精确本机抽帧/分段、质量/成本/安全证据和 Admin 治理面。

仍需依赖真实 provider 可用性、账户权限、额度和各供应商协议完成在线生成；Phone 没有完整 Canvas；ComfyUI 通用 workflow 执行尚未实现。Mock、dry-run 或 provider 可达性探测不能代替真实媒体生成验收。
