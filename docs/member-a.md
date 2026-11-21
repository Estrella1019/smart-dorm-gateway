# 组员 A：模拟设备

负责 `devices/` 下的三种软件模拟设备，以及它们的心跳、断线重连、重复命令处理和异常模拟开关。本文基于 integration 分支（46998e5）编写。

## 文件

| 文件 | 内容 |
|---|---|
| `devices/base.py` | TCP 设备公共底座：注册、收命令、回结果、心跳、重连、去重、异常开关 |
| `devices/light.py` | 灯：状态 `{"power": bool}`，动作 `set_power` |
| `devices/door_lock.py` | 门锁：状态 `{"locked": bool}`，初始为锁着，动作 `set_locked` |
| `devices/sensor.py` | 温湿度传感器：UDP 上报，带 `session_id` 和 `seq` |
| `scripts/device_acceptance.py` | 用真实进程跑一遍设备验收，见文末 |
| `tests/test_devices.py` 等 | 单元测试和网络测试 |

新的 TCP 设备只要继承 `TcpDevice`，实现 `state()` 和 `execute()` 即可，心跳、重连、去重和异常开关都会自动带上。

## 启动

在仓库根目录、每个程序单独开一个终端（网关或 `web.server` 按 [integration.md](integration.md) 先启动）：

```bash
python -m devices.light --config config.example.toml
python -m devices.door_lock --config config.example.toml
python -m devices.sensor --config config.example.toml
```

常用参数：

| 参数 | 设备 | 默认 | 说明 |
|---|---|---|---|
| `--device-id` | 全部 | `light_001` / `lock_001` / `sensor_001` | 同时开多个设备时要用不同编号 |
| `--host` | 全部 | 配置里的 host | 网关在另一台电脑上时填它的局域网 IP |
| `--heartbeat-interval` | 灯、门锁 | 5 | 心跳间隔（秒）；网关默认 15 秒没有心跳就判离线 |
| `--retry-max` | 灯、门锁 | 30 | 断线后重连等待时间的上限（秒） |
| `--no-reconnect` | 灯、门锁 | 关 | 断线后直接退出，不重连 |
| `--interval` | 传感器 | 5 | 上报间隔（秒） |
| `--udp-port` | 传感器 | 配置里的 udp_port（9001） | |

`--help` 可以看到完整列表。按 `Ctrl+C` 退出。

## 可靠性行为

- **心跳**：注册成功后每隔 `--heartbeat-interval` 秒发一次 `HEARTBEAT`。心跳和命令结果共用一条连接，加了发送锁，不会把两条消息搅在一起。
- **断线重连**：连接断开（网关重启、网络中断、心跳超时被网关关闭）后自动重连，等待时间按 1、2、4、8… 秒翻倍，最多 `--retry-max` 秒；连上后从 1 秒重新算。设备的内存状态在重连之间保留，重新注册时上报的是最新状态。
- **重复命令不重复执行**：设备记住最近 100 个 `request_id` 及其结果。同一编号再来时不执行，直接回放上次的结果，并打印 `duplicate request_id ...; replaying earlier result without executing`。这和组员 B 的“超时后用同一编号重试”配合，保证确认丢失时命令也只执行一次。
- **传感器**：每个 UDP 数据报是一条不带长度头的 JSON `REPORT`，温湿度放在 `payload.state`。每次启动生成新的 `session_id`，`seq` 每条加 1，网关据此识别重启、重复和乱序。温湿度按小步随机游走变化（温度 15–35℃，湿度 20–90%）。

## 异常模拟开关

用于演示和测试网关在设备异常时的表现，默认全部关闭。

| 开关 | 设备 | 效果 | 网关应有的表现 |
|---|---|---|---|
| `--delay 秒` | 灯、门锁 | 每条命令先等这么久再处理 | 超过 `command_timeout`（3 秒）时第一次等待超时，重试期间收到迟到回复，最终成功 |
| `--drop-ack 概率` | 灯、门锁 | 命令照常执行，但按概率不回结果 | 用同一编号重试；全部丢失时返回 `unknown`；设备只执行一次 |
| `--disconnect-after 秒` | 灯、门锁 | 每次连上这么久后主动断开，再自动重连 | 在线 / 离线来回切换 |
| `--heartbeat-interval 0` | 灯、门锁 | 不发心跳，连接保持但不说话 | 心跳超时后标离线并关闭连接，设备随后重连 |
| `--drop-rate 概率` | 传感器 | 按概率不发出某条上报，`seq` 照样递增 | `seq` 出现跳号，正常数据照常接收 |

给组员 B 的异常演示可以直接用：

```bash
# 无回复：网关重试 3 次后返回 unknown，灯的终端里出现两次 duplicate，说明只执行了一次
python -m devices.light --config config.example.toml --drop-ack 1

# 延迟回复：开灯约 4 秒后成功，说明重试收到了迟到的结果
python -m devices.light --config config.example.toml --delay 4

# 一半确认丢失：连续开关多次，大多数成功，少数 unknown，状态变化次数等于命令次数
python -m devices.light --config config.example.toml --drop-ack 0.5
```

## 验证记录

- 时间：2026-11-19
- 环境：macOS 27.2，Python 3.11.9，aiohttp 3.14.5（单元测试中的网页部分需要）
- 代码：integration（46998e5）+ 本分支的异常开关

### 自动化测试

`python -m unittest discover -s tests -v`：54 项全部通过，连续运行 8 次结果一致。另外在组员 D 的 `feat/logging-tests` 分支上合入本分支设备代码后运行，61 项全部通过（8 次）。

### 真实进程验收

`python scripts/device_acceptance.py` 让网关、灯、门锁、传感器各自作为独立进程运行，按下表结束和重启进程。为让离线判断快一些，脚本把心跳超时设为 4 秒，设备心跳 1 秒。连续运行两次，13 项均通过。

| 项目 | 结果 |
|---|---|
| 三类设备上线 | 灯、门锁为 TCP，传感器为 UDP，全部在线 |
| 开灯 / 关灯 | 成功，网关状态与设备一致 |
| 开锁 / 上锁 | 成功，网关状态与设备一致 |
| 温湿度更新 | 读数持续变化 |
| 灯退出后重启 | 退出后网关立即标离线；重启后自动上线，状态复位为关（进程内存不保存），可继续控制 |
| 传感器退出后重启 | 停止上报约 4 秒后标离线；新 session 重启后重新上线 |
| 网关重启 | 灯、门锁、传感器都自动回来，灯（开）和门锁（开锁）保持重启前状态 |
| 无回复（`--drop-ack 1`） | 约 9 秒后返回 `unknown / confirmation_timeout`；同一编号到达 3 次，2 次识别为重复只回放 |
| 重复命令不重复执行 | 上一项中灯只真正执行了 1 次；网关未收到确认，不更新状态 |
| 延迟回复（`--delay 4`） | 约 4 秒后成功：第一次等待超时，重试期间收到迟到回复 |
| 定时断线（`--disconnect-after 3`） | 12 秒内连上 4 次，网关在线 / 离线切换 6 次 |
| 不发心跳（`--heartbeat-interval 0`） | 约 4 秒后标离线，设备随后自动重连 |
| 传感器丢包（`--drop-rate 0.5`） | 约一半上报未发出，`seq` 跳号，网关照常接收，传感器保持在线 |

另做了一次网关内联测：灯丢失一半确认、连续 20 条开关命令，网关含重试共发出 32 次，灯的状态恰好变化 20 次，18 条成功、2 条 `unknown`（三次确认全部丢失）。

## 已知限制

- 设备状态只在内存里。设备进程重启后回到初始状态（灯关、门锁锁上），去重记录也会清空；网关此时已把未完成命令判为 `unknown`，不会对新连接重发旧编号。
- 去重只记最近 100 个编号，足够应对网关的几次重试，不适合长时间之后的重放。
- `--drop-ack` 对重复命令的回放同样生效，用来模拟“所有回复都丢了”。
- 传感器上报没有身份认证，只适合课堂局域网演示。
- 浏览器验收、多机和长时间负载测试不在本文范围内（分别由组员 C、D 负责）。
