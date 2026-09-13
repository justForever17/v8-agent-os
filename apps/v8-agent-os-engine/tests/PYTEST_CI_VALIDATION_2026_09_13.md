# Engine pytest 与 CI 分片验收（2026-09-13）

基线提交：`21f5b3c0418c9d3ad14e60d8b78a6342d6700728`。本次接续已授权的测试失败整改；没有迁移共享 realtime schema、修改 provider 配置或发布新版本。

## 修复与反例

- Creative Runtime 只管理五个媒体执行 facade，默认只读发现 `creative_media_capabilities` 不再因 runtime 注册顺序从 Supervisor 工具面消失。进程内恢复旧 descriptor 后，同一合同测试失败；恢复修复后通过。
- 普通工作区 `application.sqlite` 不再因上层目录受保护被误判。canonical runtime 文件、数据库旁路文件和认证目录仍先于工作区例外阻断。补充的四个祖先工作区反例在原候选补丁下全部失败，修正后通过；混合文件组在窗口绑定或粘贴执行前整体中止。
- managed CLI Skill 审计按安装方式核对 revision，修复自定义安装器与 npm semver 判据冲突；GDA 必须指向已有安装入口且源码包版本固定，未知/缺失安装器和可变版本均拒绝。Admin 中英文清除退役术语。
- 迁移 SafetyDecision、ModelHub、Research 引用/授权、Memory 工作区身份、Spec session/run 外键和旧提示词合同夹具。Spec standalone dry-run 在导入 Engine 前指定临时状态库，子进程测试证明不会创建调用者配置的状态目录。
- 删除两份测试在收集阶段注入的全局 BeautifulSoup/Scrapling 假模块。旧注入导致分片下 16 项失败，并让一项 fixture 误入真实 reader 回退；该回退入口现由用例显式阻断。
- ACP 子进程复用 `sys.executable`，Spec 路径 fixture 使用 `tmp_path`，去掉本机 `.venv` 和 Windows 分隔符假设。
- CI 增加四个必需 pytest 分片及 Node 22、Python 3.12 和 Linux 构建依赖。分片自动发现全部 `test_*.py`，按统一换行后的大小分配；旧磁盘字节算法被换行反例击败。当前 401 个文件在混合 CRLF/LF 与全 LF 模拟下分片完全一致。

## 本机验证

Windows、Python 3.12.10；普通 pytest 使用 `tests/conftest.py` 的隔离状态根，不传 `--live`。

```powershell
apps/v8-agent-os-engine/.venv/Scripts/python.exe apps/v8-agent-os-engine/tests/scripts/run_pytest_shard.py --shard-index <0..3> --shard-count 4
```

| 分片 | 测试文件 | passed | subtests passed | skipped | JUnit 耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 | 98 | 1552 | 61 | 0 | 176.282 s |
| 1 | 100 | 1384 | 0 | 3 | 297.783 s |
| 2 | 101 | 1396 | 25 | 0 | 109.517 s |
| 3 | 102 | 1676 | 0 | 0 | 145.671 s |
| 合计 | 401 | 6008 | 86 | 3 | — |

四片退出码均为 0，未遗漏接管前全量报告中的用例；新增 8 个用例。分别核对测试文件分片并集、交集、用例身份和执行前后 SHA-256，没有重复文件、遗漏或执行期间源码漂移。JUnit 的 suite tests 数包含 unittest 子测试，不能直接当作 pytest 顶层用例数。之后仅收紧 managed CLI 审计及其反例，按影响定向复测；文件大小改变后的分片布局不作为上述四片的同一快照。

附加验证：新增边界/CLI/dry-run 定向集合 37 passed；按污染源优先顺序执行网页、ACP 和 Spec 合同集合 113 passed；分片器集合 5 passed；最后的 CLI pin 审计、Godot setup 与 catalog trust 集合 17 passed；Admin i18n 5247 个键通过；变更 Python AST、workflow YAML 和 `git diff --check` 通过。

## 验证边界

额外 Linux 探测：WSL Ubuntu 22.04，隔离 CPython 3.12.14、uv 0.12.13、Node 22.23.2。完整 `requirements/test.txt` 解析 177 个包后在 pycairo 1.29.1 构建失败，原因是本机没有 cc/gcc/clang；该 WSL 未安装 workflow 已声明的构建依赖。窄依赖环境（pytest/httpx/pydantic）下 ACP stdio 10/10 通过，Spec audit 10 passed、9 failed，失败均为导入缺少 PyYAML。这只证明部分 Linux 传输合同，不能作为完整 Linux CI 通过。没有修改系统依赖或默认 Python。

以上是本机单元、合同、隔离子进程与分片执行证据，不代表真实 provider、真实剪贴板、Preview、安装包或远端 GitHub CI 验收。当前没有更改共享事件合同，也未验证其他物理平台。远端 CI 与发布状态应以实际提交的运行结果为准。
