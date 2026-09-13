# 扩展管理窄屏动作行定向闭环

**c03b7ebb复验发现的“刷新扩展”裁切和保存状态竖列，在新生产包的浅/深两主题、390px/桌面四组合中均已修复。** 头部导航、保存/刷新点击以及长说明footer的定向检查全部通过；原失败报告保留。

产品Admin源码 `829b34a33cacfeea89bbf4016a3e832ebfdd9c3f`，修片 `40bd0f79c53d093ac55f66f5f29c26c361f0f289`；交接完整HEAD `fab91be281cc18b1050f67291a10f163381beaad`。本lane独立核对829..fab的Admin/product-ui差异为空。22938生产standalone，BUILD_ID `YCv4rLZX9MWz2m6VtOPfj`；继续使用同一隔离状态和公开合成owner。未访问22928模型live或真实Engine，所有业务API仍在合成边界内。

| 判据 | 原冻结c03b7ebb | 新冻结829b34a3实测 |
|---|---|---|
| X15 390px刷新按钮 | 浅/深均x371+w105→476，clip右边390，86px被裁 | 浅/深均x128、y194、105×35，完整位于viewport和祖先clip内 |
| 保存状态 | 窄屏被挤成逐字竖列 | 实际文本Range仅1个line rect；“自动选择策略：未变更”120×18，四组合均单行 |
| 桌面头部动作 | 可达 | 继续可达，没有将窄屏修复变成桌面裁切 |
| X14商店→管理导航 | 同一shell已通过 | 两主题/两尺寸topbar几何、颜色、字体及tokens一致，移动导航选中后关闭 |
| X05 100段README footer | 组合包已通过 | 两主题/两尺寸操作及“已是此版本”结果仍在viewport内 |
| X16实际坐标点击 | 本轮新增以核验受影响动作 | 按可视按钮中心点击，不依赖自动滚动；4次保存POST/已保存状态可见，unknown false/0保留；4次刷新请求确实到达受控503，失败反馈可见 |

原X15对每个header按钮与viewport/祖先clip的交集判据未放宽。新增单行检查直接验证保存状态不拆字；新增X16证明按钮修复后能收到指针操作并显示结果。503是有意设置的合成失败，截图中的“刷新失败”是该反例预期，未宣称真实刷新成功。没有重跑全部40routes或既已通过的graph/扩展竞态全组。

单次定向run为4PASS、0FAIL、0HARNESS_ERROR、0uncaught pageerror；97个fixture API请求，1次合成安装准备footer、4次合成配置保存、4次受控reload503。源码修片只改动作组wrap/min-width及保存状态nowrap，未触公共组件。报告 `reports/integration-829b34a3-review.json` 保留所有动作bounds、保存状态line rect、实际点击、静态资源hash和截图hash。原始合成证据在本worktree `tmp/experience-9131/extensions-candidate-829b34a3/`；旧c03报告及截图未修改。

```powershell
python -X utf8 scripts/experience/9131/verify-extensions-candidate.py --candidate 829b34a33cacfeea89bbf4016a3e832ebfdd9c3f --url http://127.0.0.1:22938 --fixture-account admin --build-id YCv4rLZX9MWz2m6VtOPfj --out tmp/experience-9131/extensions-candidate-829b34a3 --only X05,X14,X15,X16
```

浏览器已关闭，22938/Admin冻结已提前明确释放给root。这里只关闭X15及其直接影响，c03时其他独立通过项仍保留为对应版本证据；没有扩大为真实Engine配置/安装、Web/Shell/最终发布通过。Phone6111 P05/P10独立闭环另见其报告；物理Android/iPhone等限制不变。无产品代码修改、push或tag。
