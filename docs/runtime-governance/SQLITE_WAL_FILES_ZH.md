# SQLite WAL 伴生文件说明

## 当前真相

`~/.v8-agent-os/state.db-wal` 与 `~/.v8-agent-os/state.db-shm` 是 SQLite WAL 模式的正常运行文件，不是配置文件、缓存脏文件，也不是迁移残留。

Engine 在 [database.py](../../apps/v8-agent-os-engine/core/database.py) 中启用了：

```sql
PRAGMA journal_mode=WAL;
```

因此，当 `state.db` 有活跃连接、写入、checkpoint 或并发读取时，SQLite 可能会创建、更新、缩小或删除这两个伴生文件。

## 文件含义

- `state.db-wal`：write-ahead log，保存尚未 checkpoint 回主数据库的写入页。
- `state.db-shm`：shared-memory index，用于 WAL 并发协调。

这两个文件周期性出现或消失属于正常行为。看到它们短暂出现，不代表 V8 Agent OS 在反复生成配置，也不代表数据库损坏。

## 排障纪律

- 不要把 `state.db-wal` / `state.db-shm` 纳入配置真相源。
- 不要把它们当作需要定期清理的异常文件。
- 如果需要备份 `state.db`，应先停止相关 Engine/Admin 进程，或使用 SQLite 在线备份方式，避免只复制主库而漏掉 WAL 中尚未 checkpoint 的数据。
- 如果文件持续增大且长时间不 checkpoint，再排查是否存在长事务、异常连接或数据库写入压力。

## 与配置文件的边界

当前配置真相仍以 `~/.v8-agent-os/config.json` 的配置域为主；`state.db*` 只属于运行状态数据库族。不要因为 `state.db-wal` 或 `state.db-shm` 存在，就推断它们参与 supervisor、memory、extensions、desktop-live 等配置解析。
