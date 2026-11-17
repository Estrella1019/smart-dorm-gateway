# 组员 D 测试记录（2026-11-17）

## 自动化测试

```bash
python3 -m unittest discover -s tests -q
# Ran 49 tests — OK（含 test_storage.py 3 项）
```

## 场景脚本（异常 / 多设备）

```bash
python3 scripts/run_scenarios.py
```

| 场景 | 结果 | 说明 |
|------|------|------|
| multi_device | 灯、门锁控制均为 success；单条控制链路写入 3 条 command 相关日志 | 多设备并行接入 |
| offline_command | status=failed, reason=device_offline | 设备离线后拒绝控制 |
| timeout_unknown | status=unknown；3 条 timeout_retry 日志 | 设备不应答时重试后结果未知 |
| udp_report | 传感器上线；device.state 日志 1 条 | UDP 温湿度上报 |

## 手工联调（本地）

- 启动 `gateway.server` + `devices.light` + `client.cli power light_001 on`
- CLI 返回 `status: success`，网关出现 `command.submitted` → `command.completed`
- `request_id` 在 CLI、网关、设备侧一致（示例：`d9e6353e84b6418b94f0f61e26ff7b7f`）

## 遗留问题

- `offline_command` 场景在「强制断连」路径下主要记 `device.disconnected`，软离线 `device.offline` 需心跳超时（约 2s 巡检周期）才出现；演示时可结合组员 A 的断线模拟说明。
- 性能阶梯（10/50 设备）尚未在本机大规模跑数；如需答辩数据可在联调后补跑并更新本表。

## 模块入口

见 `docs/member-d.md`。