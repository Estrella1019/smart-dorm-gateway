# 组员 C：运行、接口与讲解

实测记录（2026-11-14）：新增网页集成测试通过。完整回归 20 项中 19 项通过，原分支 test_member2.test_command_queuing 失败：预期第二条命令 value=False，实际收到 True，可能读到重试消息，尚未定位。未做浏览器人工验收或门锁/UDP 端到端测试。Git 克隆两次连接超时，尚未上传远程。

## 启动与验收

Python 3.11+。在仓库根目录运行：

```powershell
python -m pip install -r requirements.txt
python -m web.server --host 127.0.0.1 --port 8080
```

另开终端运行 `python -m devices.light`（设备参数见 `python -m devices.light --help`），浏览器访问 http://127.0.0.1:8080 。网页启动器已经启动 TCP 网关，不要同时启动 `gateway.server` 占用 9000 端口。原配置通过 `--config config.example.toml` 传入，不修改原配置契约。

运行测试：`python -m unittest discover -s tests -v`。手工验证：开灯、关灯，检查模拟灯终端与网页状态一致；关闭设备进程观察离线，重新启动观察在线；停止网页服务观察连接中断，重启观察自动重连。浏览器人工验收需另行执行。

## 交付范围

11 月 10 日：HTTP 列表、真实网关灯控制、网页反馈已实现。11 月 15 日：WebSocket 和重连已实现；传感器状态以原样 JSON 显示，门锁提供 set_locked 薄适配入口，但实际端到端验收依赖组员完成设备及网关支持。

当前仓库是下载的 jinxinyi-reliable-control 分支压缩包快照，没有 Git 提交历史；没有提交或推送远程修改。

## 已核对的服务契约

- `Gateway.list_devices()` 同步返回字典列表，注册表当前只保存在线灯。
- `submit_command(device_id, action, value)` 异步等待设备结果，当前仅支持 set_power + bool，返回 request_id/status/state?/reason?。
- `send_to_device(device_id, message)` 仅负责发送，不保证执行成功。网页不直接调用它。
- `EventBus.subscribe(callback)` 同步回调；适配层仅向有界队列入队，不在回调里等待网络。
- 网关原启动为 `python -m gateway.server`；新入口在同一事件循环创建原 Gateway，不重写 Socket。

## HTTP / WebSocket

GET `/api/devices` 返回 `{devices:[{device_id,device_type,online,state,last_seen,...}]}`。
POST `/api/devices/{id}/commands` 请求 `{action:"set_power",value:true}` 或 set_locked，返回网关原结果。400 参数非法；422 当前网关不支持；200 的 failed/unknown 仍须按业务结果解释。HTTP 请求持续等待确认，网页立即显示执行中。
GET `/ws` 升级连接，首次及每次事件返回 `{event:{event,timestamp?,...},devices:[...]}`。事件沿用 device.connected/disconnected/state/heartbeat、command.submitted/completed；首次 event 为 snapshot。每连接队列 32 条，拥塞时丢最旧消息，最新消息包含完整快照用于恢复状态；不承诺事件审计完整性。

重连后重新获取快照。只保留本次服务运行见过的离线设备，无持久历史；服务重启后离线历史丢失。断开 WebSocket 时禁止控制并提示状态可能过期。设备状态只来自注册表，不因点击按钮而乐观修改。

## 需要其他组员配合

组员 1/组长补 UDP REPORT、温湿度设备、门锁注册与控制协议；组员 2 确认可靠控制行为，并确认门锁使用 set_locked/bool 或在适配层修改映射。统一 device_type（建议 light/lock/sensor）、state 字段、事件名称，保持 list_devices / submit_command 签名。当前分支已有心跳超时、排队和有限重试；网页直接订阅相应事件。组员 4 提供日志查询接口后再暴露 HTTP，不重复实现日志。新增 aiohttp 依赖需小组集成时确认。

## 中文逐段讲解与答辩

第一段：我的模块把浏览器和网关连接起来。HTML 展示设备，HTTP 查询和提交控制，WebSocket 推送变化。TCP/UDP 由原网关维护，我没有另建设备连接。

第二段：`create_app` 接收 Gateway，体现依赖注入。HTTP 处理器校验布尔值和动作，再 await submit_command。服务返回成功前必须收到设备 COMMAND_RESULT，网络发送成功不能代替执行成功。

第三段：事件回调向每个浏览器的有界队列放消息，发送协程负责网络传输。这避免慢浏览器阻塞设备处理；完整快照帮助客户端在丢消息和重连后恢复一致。

第四段：网页点击后显示执行中，收到结果再显示成功、失败或未知。超时说明没有收到确认，设备可能已经执行，因此不能简单显示执行失败，也不能无条件重试开锁。

数据流：按钮 → HTTP → submit_command → 原 TCP Socket → 灯执行 → COMMAND_RESULT → pending Future 完成 → HTTP 结果；同时 EventBus → 有界队列 → WebSocket → 网页状态刷新。传感器未来走 UDP → 注册表/事件 → 同一网页链路。

教授问：为什么用 HTTP 和 WebSocket？答：HTTP 适合明确的查询和提交，WebSocket 让网关主动通知状态，减少轮询。

教授问：TCP 可靠是否代表设备执行成功？答：不能，TCP 保证字节流传输，执行语义必须依赖应用层确认。

教授问：如何处理确认丢失？答：显示结果未知，由可靠控制层负责同请求号重试和设备去重，再通过状态上报核对；当前分支已实现有限重试，设备端去重仍需核对。

教授问：断网恢复如何保证一致？答：重建 WebSocket 后读取完整快照；断网期间显示状态过期并禁止操作。

教授问：是否完成全部设备？答：目前只可实测灯；门锁和传感器界面适配已预留，网关尚无对应设备支持，不能把页面存在当作端到端完成。

教授问：可以直接用于真实门禁吗？答：课程计划限定本地模拟系统，没有鉴权和生产门禁安全机制，默认只监听回环地址。


## 三分支核对结论（2026-11-14）

main 为基础骨架；jinxinyi-reliable-control 增加软离线、串行排队及相同请求号重试；feat/device-simulators 增加 TcpDevice、DoorLock 和 UDP Sensor，但该分支 gateway/server.py 仍只接纳 light，没有 UDP 接收器。因此设备端已完成不代表已接通网关。

门锁实际 device_type=door_lock，动作 set_locked，状态 locked；网页已兼容该名称。传感器 device_type=sensor，state.temperature/state.humidity；REPORT payload 另有 session_id、seq，UDP 默认 9001。网页展示原始状态，不自行生成温湿度。

阻塞项：组长/网关负责人合并设备分支并增加 door_lock 注册、按类型验证状态、set_locked 路由，以及 UDP REPORT 接收/设备注册/事件发布和 session_id+seq 处理。组员2确认门锁同样通过 submit_command 获得可靠控制并协调心跳间隔（设备默认5秒、网关阈值5秒，可能边界误判）。组员4日志接口暂不影响网页基础功能，但未提供时无法做日志查询。组员C不重写这些 Socket 模块。

可验收：真实列表与灯控制、HTTP 参数检查、WebSocket 快照；待联合验收：门锁、UDP温湿度、离线恢复与重试/去重。不要将测试替身的数据当作真实设备接入证明。

新增复核：真实 TCP 灯控制、WebSocket command.completed、device.disconnected 以及 WebSocket 重连离线快照测试通过。上传尝试再次因 github.com:443 连接失败而中断，尚未进入账号认证阶段。

上传准备：已成功取得可靠控制分支 Git 工作副本，交付改动仅包含网页、依赖、网页测试、文档及 CI 依赖安装步骤。
