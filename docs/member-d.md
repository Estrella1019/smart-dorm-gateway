# 组员 D：日志与系统测试

## 已实现内容

- `gateway/storage.py`：订阅 EventBus，异步队列写入 SQLite（`data/events.sqlite`）
- 查询 API：`query_recent`、`query_by_request_id`、`query_command_trail`
- 网关启动：`python -m gateway.server`（默认 `--log-db data/events.sqlite`）
- 网页查询：`GET /api/logs?limit=50`，`GET /api/logs?request_id=<id>`（需 `python -m web.server`）
- 命令行查询：`python -m client.log_cli recent --limit 20`、`python -m client.log_cli trail <request_id>`
- 场景脚本：`python scripts/run_scenarios.py`（多设备、离线、超时、UDP 上报）

## 记录的事件类型

设备上下线：`device.connected` / `device.disconnected` / `device.offline` / `device.heartbeat`  
状态上报：`device.state`（含 UDP 温湿度）  
控制链路：`command.submitted` → `command.sent` →（可选）`command.timeout_retry` → `command.completed`

## 本地自测

```bash
python3 -m unittest discover -s tests -q
python3 scripts/run_scenarios.py
```

场景结果 JSON 可粘贴到组内测试汇总；数据库文件在 `data/scenarios/` 与各次运行目录下（已在 `.gitignore`）。

## 提交 PR

在分支 `feat/logging-tests` 上开发，目标分支选 **integration**。

```bash
git checkout -b feat/logging-tests
git add gateway/storage.py gateway/server.py web/server.py client/log_cli.py scripts/ tests/test_storage.py docs/member-d.md
git commit -m "feat: persist gateway events and add system test scenarios"
git push -u origin feat/logging-tests
```

然后在 GitHub 上创建 Pull Request → `integration`。

## 汇报时可演示

1. 启动网关与设备，执行一次网页或 CLI 控制  
2. `python -m client.log_cli trail <刚才返回的 request_id>` 展示完整控制日志  
3. 运行 `scripts/run_scenarios.py`，说明多设备与异常场景结果