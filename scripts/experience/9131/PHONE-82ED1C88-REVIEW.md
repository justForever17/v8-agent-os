# Phone 82ed1c88 独立原生验收

**原生多连接切换被 VideoPlayer 生命周期错误阻断，不能报 Phone 多设备验收通过。** 首次配对 B 后、Retry 恢复再从 B 切回 A，均进入全屏错误页。已将精确步骤和证据交给 Phone/协调任务，AVD 已明确释放，当前没有本 lane 的 ADB harness 运行。

冻结产品 `82ed1c88f04881dcde5932bf39bd70cb0497c3e2`，Android 14、x86_64、独立 AVD `emulator-5586`，release APK（基线版本名9.12.1）。文件与安装 APK 的 SHA256 均独立核对为 `A58E5BD339B96AF9A914C0197ADE8625E18621BE70FD1CEED8F88125096A7888`；APK 对应源码提交来自构建 owner 交接，hash 相同不额外证明构建可复现。05f8 后续窄修尚未包含在这个 APK。

合成 API 是冻结82ed的 `tests/fixtures/phone-api-fixture.cjs`，端口22836。A/B 具有不同 verified instanceId，但刻意共用 principal、session-1 和 message-1。实际经原生备用配对链接添加B，没有注入本地 profile/凭据数据库。没有读真实配置、凭据或日志，没有清应用数据；22826 Expo Web dev 未被用作本轮证据。

| 反例 | 独立可观察结果 | 判定 |
|---|---|---|
| P01 重复选当前A1，A1→A2→A1 | 输入A1后重复选择不清空；A2输入独立；返回A1恢复原字符串并显示A消息 | NATIVE_SIMULATOR PASS |
| P04 首次原生配对B | 点“连接并进入 V8 OS”后全屏 `VideoPlayer.pause` rejected / `Cannot use shared object that was already released` | NATIVE_SIMULATOR FAIL |
| R04 错误恢复和再次切换 | Retry能回B首页；选B task1显示B消息，输入B1成功；B切回A再次触发同一错误 | 恢复部分成立；切换FAIL |
| P05 完整ABABA、P10分别以A/B活动连接杀进程恢复 | 被上述切换故障阻断 | NOT_RUN |
| S01 SecureStore写失败 | 拒绝外显、错误去敏，没有假保存 | BOUNDARY_EXECUTED PASS |
| S02/S03 profile目录失败、并发、旧凭据激活 | 目录失败保留A/旧凭据，清新槽并可重试；并发增加B/C不丢更新；旧ref与活动指针写失败均不发布激活 | BOUNDARY_EXECUTED PASS |
| S04 草稿写失败及迟到提交清空 | input/files/plugins/selection保留；旧composer revision不能清新输入；重试持久化后新store精确恢复 | BOUNDARY_EXECUTED PASS |
| S05 同ID、大小写ID、cursor/tombstone | 真实内存SQLite执行实际服务方法；A tombstone不影响B，A迟到消息不能复活，A删除不清B cursor | BOUNDARY_EXECUTED PASS |

源码候选根因在82ed `src/components/personalization/PhoneBackgroundMedia.tsx`：`useVideoPlayer`之后注册的effect cleanup无条件`player.pause()`，即使空背景也创建player。实测证明切连接触发“已释放对象”异常；具体SDK释放顺序及最终修法由Phone核对。没有把播放器源码猜测当作最终修复证明。`MediaRenderers.tsx`的音频也有类似cleanup形状，只列待核对线索，不算本轮音频实测失败。

首次R04脚本错误地等待新profile首页出现editor；实际首页要求先选工作区。这是harness适配问题，已将新profile的等待条件改为导航入口，再经实际B task1进入会话。原记录保留在忽略目录，不计作产品失败。有效R04第二run完成B消息/输入观察后，在真实B→A切换上复现播放器异常。P04首次错误页和R04第二次错误页都保留截图/XML，不因调整等待条件撤销故障。

结果摘要见 `reports/phone-native-82ed1c88-review.json`、`reports/phone-storage-82ed1c88.json`；截图及原始合成UI输出在本worktree `tmp/experience-9131/phone-native-82ed1c88/`。SQLite为Node22.22.0的真实SQLite3.50.4内存库；SecureStore/metadata故障是受控替身，未在Android Keystore或真实磁盘注入故障。原生自动化耗时不作为帧率、首响或优化比例。

```powershell
python -X utf8 scripts/experience/9131/verify-phone-native.py --live --serial emulator-5586 --candidate 82ed1c88f04881dcde5932bf39bd70cb0497c3e2 --apk E:/Projects/v8chat/.codex-worktrees/9131-phone/apps/v8-agent-os-phone/android/app/build/outputs/apk/release/app-release.apk --apk-sha256 A58E5BD339B96AF9A914C0197ADE8625E18621BE70FD1CEED8F88125096A7888 --out tmp/experience-9131/phone-native-82ed1c88 --only P01
# 仅一个A profile时先P04；失败后的恢复单独用R04。不能在已有B时盲目重复配对。
node scripts/experience/9131/verify-phone-storage-boundary.mjs --candidate 82ed1c88f04881dcde5932bf39bd70cb0497c3e2
```

最终需复验包含生命周期修复、05f8与realtime0.0.46的冻结APK。实体Android、iPhone、原生长流内存/热量、电池、真实mesh/provider仍未验证；这5项存储边界通过不能抵消原生切换故障。未修改产品源码，无push/tag、共享Preview或最终发布通过声明。
