# Phone 49db49ea 最窄原生主流程复验

**新传输/身份检查候选的原生ABABA与一次A活动态进程重启均通过。** 本次确认这些改动没有再次破坏已验证的消息/草稿归属和主入口，不扩展为全部传输故障、LAN或物理机验收。

产品 `49db49eaa1223f18308198727b1adabf8df6e9a9`；冻结fixture `dfa8b5e750e67440fcfbfefbf2d7777c89a56ae9`的phone-api-fixture.cjs，22836 `--native-final`。Phone owner确认临时C已移除、offlineC/holdBodiesC关闭、A/B原Independent草稿保留，并把独立AVD emulator-5586交给本lane。Android14 x86_64 release，realtime0.0.46由构建owner交接。

冻结副本 `phone-transport-native/phone-49db49ea-realtime46.apk` 与设备安装APK的SHA256均由脚本独立比对为 `70FFA5FD9188AD5E1B9773C339D2F7546C7E309464B683D557A0A1B9D2E5049B`。没有卸载、重新配对、编辑凭据、注入新profile或mock网络响应；原生应用通过已交接的合成HTTP fixture访问服务。

| 原判据 | 独立实测 |
|---|---|
| P05 A→B→A→B→A | 四次切换均显示正确A/B synthetic message与各自Independent_A1/B1草稿；同session-1/message-1/principal时未串用，无播放器异常 |
| P10A一次force-stop/start | 活动A/session1在进程重启后仍显示A消息和Independent_A1_9131_UNSENT，无重新输入配对、无清数据 |

脚本输出 `reports/phone-native-49db49ea-review.json`，保留文件/安装hash、fixture完整commit、实际结果和截图hash；原始截图/XML在experience worktree `tmp/experience-9131/phone-native-49db49ea/`。P05/P10A自动化耗时93.91s/18.21s包含ADB/UIAutomator往返，不能作原生渲染延迟或性能比较。fixture命令最初传短hash，报告另用Git独立解析并记录完整同一对象，不因元数据补全重跑测试。

```powershell
python -X utf8 scripts/experience/9131/verify-phone-native.py --live --serial emulator-5586 --candidate 49db49eaa1223f18308198727b1adabf8df6e9a9 --fixture-commit dfa8b5e750e67440fcfbfefbf2d7777c89a56ae9 --realtime-version 0.0.46 --apk E:/Projects/v8chat/.codex-tmp/release-20260913-1/phone-transport-native/phone-49db49ea-realtime46.apk --apk-sha256 70FFA5FD9188AD5E1B9773C339D2F7546C7E309464B683D557A0A1B9D2E5049B --out tmp/experience-9131/phone-native-49db49ea --only P05,P10A
```

P10A是协调明确要求的一次A重启，原P10双活动态判据未改动；本次没有重复6111长流、wrong alias零凭据、body并发/取消、LAN/蜂窝或实体Android/iPhone。那些新传输边界仍按Phone作者报告的证据层级陈述。脚本已退出，AVD/22836冻结已明确释放给Phone，最终A1；没有访问默认端口的package-state-1。无push/tag或最终发布通过声明。
