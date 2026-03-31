# Script Migration Map

这份映射表定义了 `v8-agent-os-engine/scripts` 里现有脚本的目标归类，用于后续物理迁移。

## runtime

- `cron_nightly_memory_batch.py` -> `runtime/cron_nightly_memory_batch.py`（已迁移，顶层保留兼容壳）
- `cron_supervisor_task.py` -> `runtime/cron_supervisor_task.py`（已迁移，顶层保留兼容壳）
- `run_engine_prod_like.ps1` -> `runtime/run_engine_prod_like.ps1`（已迁移，顶层保留兼容壳）

## tools

- `cleanup_obsolete_runtime_artifacts.py` -> `tools/cleanup_obsolete_runtime_artifacts.py`（已迁移，顶层兼容壳已删除）
- `repair_memory_index.py` -> `tools/repair_memory_index.py`（已迁移，顶层兼容壳已删除）
- `offline_visual_parser_doctor.py` -> `tools/offline_visual_parser_doctor.py`（已迁移，顶层保留兼容壳）
- `tools/import_external_legacy_v8chat.py`

## regression

- 所有 `*_regression.py` 已迁入 `.tmp-tests/archive/`
- 典型示例：
  - `runtime_stability_*_regression.py`
  - `computer_use_*_regression.py`
  - `rpa_*_regression.py`
  - `trace_schema_v2_regression.py`
  - `supervisor_*_regression.py`

## validation

- 所有 `*_validation.py` 已迁入 `.tmp-tests/archive/`
- 所有 `*_smoke.py` 已迁入 `.tmp-tests/archive/`
- 所有 `*_probe.py` 已迁入 `.tmp-tests/archive/`
- 所有 `*.sample.json`
- 典型示例：
  - `context_acceptance_validation.py`
  - `context_prod_like_validation.py`
  - `memory_acceptance_validation.py`
  - `memory_prod_like_validation.py`
  - `computer_use_primitive_live_validation.py`
  - `computer_use_primitive_smoke.py`
  - `computer_use_pure_visual_qq_my_phone_send_probe.py`

## legacy

- 原 `legacy/` 目录已清仓，不再保留仓库内 legacy 脚本归档。
- 如需追溯历史阶段方案，应以 `docs/历史文档/` 和当前活跃回归脚本为准。

## 当前约束

- 这轮先建立目录边界、索引和迁移规则。
- 仅仍有活跃兼容价值的顶层脚本可以继续保留；失去主链价值的旧脚本已迁到 `.tmp-tests/archive/`，避免继续误导调用方。
- 后续物理移动时，必须同步修正：
  - 文档链接
  - Python 导入路径
  - action target
  - 外部脚本引用
