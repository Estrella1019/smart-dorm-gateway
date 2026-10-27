# 智能宿舍多协议网关

网络编程课程设计，五人小组。**纯软件模拟设备，不需要硬件。**

这是组长考前交接用的可运行骨架：TCP 网关、统一应用层协议、模拟灯和命令行控制已实现。最终目标是 TCP/UDP 多协议网关；**目前 UDP、网页、心跳超时、自动重连、重试去重与持久化尚未实现**，由后续阶段补齐。

## 快速启动

需要 **Python 3.11 或更高版本**。当前代码仅使用标准库，无需 `pip install`。以下命令均在本项目根目录执行，Windows 可用 `py -3` 替换 `python3`。

终端 1：启动网关。

```bash
python3 -m gateway.server
```

终端 2：启动软件模拟灯。

```bash
python3 -m devices.light --device-id light_001
```

终端 3：查看设备并控制。

```bash
python3 -m client.cli list
python3 -m client.cli power light_001 on
python3 -m client.cli power light_001 off
```

控制成功返回 `COMMAND_RESULT`，其中 `payload.status` 为 `success`，`payload.state.power` 为实际模拟状态。模拟灯终端也会打印结果。再次执行 `list` 可查看最新状态。结束时各终端按 `Ctrl+C`。

需要第二盏灯时，另开终端运行 `python3 -m devices.light --device-id light_002`。设备编号不能与在线设备重复。

## 配置与局域网联调

默认监听 `127.0.0.1:9000`。自定义配置可复制 `config.example.toml` 为 `config.local.toml`，然后在三个程序中使用 `--config config.local.toml`（客户端选项放在 `list` / `power` 之前）。

```bash
python3 -m gateway.server --config config.local.toml
python3 -m devices.light --config config.local.toml --device-id light_001
python3 -m client.cli --config config.local.toml power light_001 on
```

跨电脑联调时，网关本地配置把 `host` 改为 `0.0.0.0`；设备和客户端通过 `--host <网关的局域网IP>` 连接。不要把 `0.0.0.0` 当作客户端目标地址。按系统提示允许局域网端口访问。

**公开源码不等于公开部署服务。**该骨架没有身份认证或 TLS，只用于本机/受控局域网实验，不应将端口开放到公网。配置文件不需要填写账号或密钥。

## 项目结构

```text
shared/       公共协议、编解码、配置
gateway/      TCP 接入、在线设备表、控制骨架、事件与服务接口
devices/      模拟灯示例；组员 1 扩展三类设备
client/       临时命令行入口
web/          组员 3 的网页入口说明，当前没有网页服务
tests/        分帧、网络集成和配置测试
docs/         协议、架构、模块交接、时间计划、GitHub 使用说明
.github/      GitHub Actions 测试配置
```

## 已实现与未实现

| 能力 | 当前状态 |
|---|---|
| TCP 分帧、字段校验、多连接接入 | 已实现，长度上限 64 KiB |
| 模拟灯注册、列表查询、开关控制与结果确认 | 已实现 |
| 请求编号、超时结果未知、断线清理 | 已实现最小版本 |
| 同设备并发指令 | 当前直接返回 `device_busy`，没有排队 |
| 在线设备表 | 当前只列连接中的设备，断线后移除；尚无离线历史 |
| UDP 传感器、门锁 | 待组长与组员 1 扩展 |
| 心跳超时、重连、重试、去重 | 消息/模块接口已预留，机制待实现 |
| HTTP、WebSocket、网页、SQLite | 尚未实现 |
| 批量设备、异常注入、性能实验 | 待组员 4 基于模拟器实现 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试会在本机随机空闲端口启动真正的 TCP 服务，验证分帧、灯控制、超时、断线、重复注册、并发拒绝以及错误消息隔离等行为。无需提前启动网关。GitHub Actions 配置在推送后运行相同命令；本地通过不代表远端 CI 已运行。

本次本地验证详情见 [框架验证记录](docs/verification.md)。

## 小组交接

- 先读 [架构与接口](docs/architecture.md) 和 [协议说明](docs/protocol.md)。
- 对照 [五人模块交接](docs/handoff.md) 认领任务。
- 按 [考试与项目时间表](docs/plan.md) 检查里程碑。
- 准备上传时读 [GitHub 发布步骤](docs/github.md)。

组长于 11 月 3 日前准备骨架；11 月 4—7 日以复习及期中考试为主；11 月 8 日起组员接手模块；11 月 23 日基本完成。具体语言与框架仍需遵守课程要求。
