# Extensions 84959e0f 定向生产复验

**X10结构化错误文案与F03 stdio参数保真缺口已在本次对应层级独立复验通过。** 原输入未削弱；另补实际表单POST对账、空数组/换行/转义字符和非法数组拒绝。结论不扩大为真实Engine/安装/业务服务或全站验收。

候选产品 `84959e0f394bc23d4e6263f36aedf51036a0afc7`，包含共享错误修复的等价cherry-pick544de130（协调08e74283）。作者确认在clean849之后构建生产standalone并启动22825，BUILD_ID `-Ir0y5eMvH8R8jpXhMOc7`。交接HEAD `b00967ccec2cd069160d6cc05048c6dcf0c4c21a` 仅修作者测试选择器；独立Git diff确认无产品差异。服务冻结期间完成，结束已通知提前释放。

| 层级/反例 | 实际结果 |
|---|---|
| 生产浏览器 X10 | GET目录502 `{detail:{message:"Independent fixture source offline"}}`；页面alert包含该具体原因，原 `[object Object]` 失败不再出现 |
| 生产浏览器 X13 | 原 `['--label','  spaced value  ','']` 在现有stdio编辑器按JSON精确展示；不修改直接“保存修改”，截获实际POST数组完全一致；unknown `false/0`保留 |
| 生产函数 F03 | 从849 Git对象AST提取真实转换函数执行，同原反例完整往返 |
| 生产函数 F04 | 7组：空数组、一个/两个空参数、嵌入LF、嵌入CRLF、引号/反斜杠、首尾空格，全部精确保留 |
| 生产函数 F05 | 4组非法JSON数组输入（数字/null/object/不完整JSON）明确拒绝，不生成配置payload |
| 生产函数 F01/F02 | 未改endpoint/header引用与unknown保留；显式清除引用/替换endpoint正确，原base对象保留 |

浏览器2PASS、函数5PASS（F04/F05内部矩阵另列），0FAIL、0未解决HARNESS_ERROR。两类证据分开，不能把函数VM当实际CLI启动或BFF凭据存储验收。浏览器使用Chrome153.0.8010.36、真实生产React；只有公开合成Owner auth经过实际本机入口，全部业务API在自己的有状态fixture中响应，外域abort、未声明请求503。未创建MCP进程、安装扩展或调用真实Engine。

此次harness仅增加候选commit/build参数、stdio配置外边界fixture和补充断言。X10仍要求可读detail.message；F03仍要求原数组逐项完全相等，未改成trim后相等。原09dfcc3c通过的来源/target/pending/footer结果仍是其对应版本的证据，本轮没有重复整套或宣称新的性能改善。

```powershell
python scripts/experience/9131/verify-extensions-candidate.py --candidate 84959e0f394bc23d4e6263f36aedf51036a0afc7 --build-id=-Ir0y5eMvH8R8jpXhMOc7 --out tmp/experience-9131/extensions-84959e0f --only X10,X13
node scripts/experience/9131/verify-extension-form-boundary.mjs --candidate 84959e0f394bc23d4e6263f36aedf51036a0afc7
```

结果 `reports/extensions-84959e0f-review.json`、`reports/extensions-form-84959e0f.json`；截图/原始合成记录在experience工作树 `tmp/experience-9131/extensions-84959e0f/`。API请求数量、错误与截获POST见JSON。脚本不会为参数中的commit切换服务器，重跑前仍须确认实际版本。

当前针对Admin/Extensions已报告的定向缺口完成对应复验；最终共享包/预加载/主题统一、整合生产UI与全部原能力仍由协调安排后继续独立验收。Phone还未收到精确最终候选，实体设备保持未验证。没有生产改动、push/tag/共享Preview操作。
