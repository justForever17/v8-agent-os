# Admin 251d31e6 定向生产复验

**d951d9cc复验发现的焦点、相机过渡、安全失败状态和治理标题/错误文案，本次5项定向检查全部通过。** 这关闭对应UI边界问题，不代表完整Admin、Engine权限/持久化、跨客户端或发布验收完成。

候选 `251d31e6e656202d5a89fd57050c1c329782b6a0`；作者交接为22828 production standalone、BUILD_ID `ZNVj7gyUrIBUI5IHJ_hO_`、Next16.2.10，源码与服务冻结。浏览器Chrome153.0.8010.36，真实React/Canvas，业务API为独立合成边界。只读Git对象核对改动，没有改变生产代码或服务。

| 原失败 | 同输入下的生产实测 | 结论边界 |
|---|---|---|
| 节点菜单关闭后焦点BODY | 键盘Workspace A→shared→关闭按钮→Escape；焦点为BUTTON，名称Workspace A12/11 | PASS：回到不会随节点菜单消失的列表入口；本次未复测所有删除/分页目标漂移 |
| 空白返回相机直接归位 | A选中r149.79→空白返回r61.52，中间半径帧15；约23.5ms后r139.89，约373ms归位 | PASS：沿原Canvas逐帧oracle确认过渡存在，不读React内部状态，不凭作者报告；不是FPS/性能比较 |
| 安全主配置503只留spinner | config-registry/safety返回明确503，仍有“安全控制”h1、无持续spinner、有重试；恢复响应后编辑/保存入口可用 | PASS：主配置失败显式外显且可恢复；没有提交真实安全设置 |
| 可选诊断503阻断配置 | 配置成功、safety/dashboard503；3个主配置控件仍呈现，保存可用，错误矩形在viewport内；重试成功后错误消失 | PASS：可选诊断失败独立；没有用空成功替代失败 |
| 治理页双h1/错误key | 只有“运行治理”一个h1；失败toast实际为“加载规则状态失败”，无原始translation key | PASS：真实错误路径触发后确认文案，不以无错误时截图证明 |

结果在 `reports/admin-251d31e6-review.json`，两run的请求/合成写/错误数量逐一记录；5PASS、0FAIL、0HARNESS_ERROR。保留Canvas时间序列与资源hash。原d951的motion/20次卸载证据保留为该版本结果，本次没有改写成251全量资源验证；最终整合版必要回归由协调安排。

作者的普通表单harness与第一run部分重叠，未同时启动连续资源计数采样。本次相机只判连续帧存在，未利用这些时刻给延迟/FPS提升结论。采样结束已通知作者可以运行其资源脚本；普通UI结束后所有本lane浏览器均关闭。

```powershell
node scripts/experience/9131/verify-admin-candidate.mjs --candidate 251d31e6e656202d5a89fd57050c1c329782b6a0 --runtime production --only G03-menu-focus,G02
node scripts/experience/9131/verify-admin-candidate.mjs --candidate 251d31e6e656202d5a89fd57050c1c329782b6a0 --runtime production --only A10,A11,A12
```

新安全/治理用例只补测试fixture与独立判据，已有焦点/镜头oracle不变。截图和原始合成输出在experience工作树 `tmp/experience-9131/admin-candidate-251d31e6/`。运行前必须确认服务实际commit，命令不会自动切换服务版本。

未验证项继续保留：最终共享包重新pack/全部消费者、统一Preview、完整原生/安装包、Phone实体及其它lane的缺口。Extensions84959e0f尚待新生产运行证据再复验F03/错误文案；本次无push/tag/生产文件改动。
