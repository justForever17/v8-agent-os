# Phone 6111d7c6 原生切换与恢复定向闭环

**此前B→A触发的VideoPlayer全屏错误，在新release APK的完整ABABA和两种活动连接进程重启中未再复现；消息、草稿和配对按身份恢复。** 本次独立关闭对应切换故障及P05/P10待测切片，不代表Phone全功能、真实设备或发布验收完成。

产品 `6111d7c6088c2c7d42beb7aee7dfa82f552558df`，fixture `a7155d620a103e4c5b83a8411c50f58006454855`，Android14 x86_64独立AVD `emulator-5586`，release。冻结APK副本 `phone-final/phone-6111d7c6-realtime46.apk` 与设备安装APK的SHA256均独立核对为 `7392BCF2FB0FA77B6389F4E306962202001760A16D9F4719E31536B26DA04A21`。产品提交及realtime0.0.46解包进入构建的关系由Phone交接；文件hash相同不单独证明嵌入依赖版本或构建可复现。Web22826仍是82ed dev，未用于这份证据。

AVD接手时保留上次真实原生配对的A/B及Independent_*草稿，合成API22836的额外terminal/submit hold/stream padding均关闭。没有重新注入profile、重输草稿、清数据或读凭据。测试结束明确归还AVD，所有本lane ADB脚本已退出，最终停在A/session1。

| 反例 | 独立结果 |
|---|---|
| P05 A→B→A→B→A | B1始终显示B synthetic message和Independent_B1_9131_UNSENT；A1始终显示A消息和Independent_A1_9131_UNSENT，另一实例消息不存在于当前页面。A/B共用principal、session-1、message-1；没有播放器错误 |
| P10 A活动时force-stop/start | 无重新配对，恢复A1草稿和A消息，再切B恢复B1 |
| P10 B活动时force-stop/start | 无重新配对，恢复B1草稿和B消息；返回A后A1/A2各自草稿仍在 |
| S01-S05新提交存储/真实SQL复验 | Native安全存储失败去敏外显；目录失败保留旧凭据、并发/过期激活保护；草稿失败重试/旧提交不清新输入；真实内存SQLite的message/cursor/tombstone/大小写ID分区均通过 |
| 新S06重启中的提交 | frozen生产PhoneDraftStore读取submitting，恢复acceptance_unknown；clientMessageId、fingerprint、session、composerRevision及unknown false/0均保留，读取期间新输入优先；恢复状态再持久化通过 |
| S06对抗对照 | 同一输入和判据运行82ed，FAIL：state仍submitting；6111 PASS。没有用源码字符串或测试数量代替行为差异 |

源码独立对照确认：PhoneBackgroundMedia不再在空背景创建player，也删除无条件pause cleanup，视频子组件仅可见时挂载。原生复验覆盖本次实际发生故障的空背景连接替换；没有把这扩写成真实视频播放/所有音视频退出路径的独立证明。本次已有A/B，因此没有再增加第三profile重测首次配对入口。

原生结果见 `reports/phone-native-6111d7c6-review.json`，截图hash及原始UI XML留在本worktree `tmp/experience-9131/phone-native-6111d7c6/`。存储结果 `reports/phone-storage-6111d7c6.json`，反例对照 `reports/phone-storage-82ed1c88-S06.json`。存储执行是冻结实际模块＋受控SecureStore/metadata替身＋真实SQLite内存库，不是Android Keystore或磁盘故障注入；S06不发网络请求，不代替原生同ID重试验收。

```powershell
python -X utf8 scripts/experience/9131/verify-phone-native.py --live --serial emulator-5586 --candidate 6111d7c6088c2c7d42beb7aee7dfa82f552558df --fixture-commit a7155d620a103e4c5b83a8411c50f58006454855 --realtime-version 0.0.46 --apk E:/Projects/v8chat/.codex-tmp/release-20260913-1/phone-final/phone-6111d7c6-realtime46.apk --apk-sha256 7392BCF2FB0FA77B6389F4E306962202001760A16D9F4719E31536B26DA04A21 --out tmp/experience-9131/phone-native-6111d7c6 --only P05,P10
node scripts/experience/9131/verify-phone-storage-boundary.mjs --candidate 6111d7c6088c2c7d42beb7aee7dfa82f552558df
node scripts/experience/9131/verify-phone-storage-boundary.mjs --candidate 82ed1c88f04881dcde5932bf39bd70cb0497c3e2 --only S06
```

最后一条命令预期返回失败以证明旧实现可被反例打败。Phone作者的长身份资源目录、A3提交中断同ID重试、terminal/reset及120秒61.2MiB流仅保留为作者验证，本lane未独立重跑。没有物理Android/iPhone、蜂窝/真实mesh、长时电耗/温升或原生帧性能结论；自动化时间不是响应性能。无产品代码、push/tag或最终发布通过声明。
