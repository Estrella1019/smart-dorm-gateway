# 应用层协议 v1

## TCP 帧

```text
4 字节无符号整数（大端序，JSON 字节数） | UTF-8 JSON
```

消息体 1—65536 字节；长度不是字符数。必须使用公共编码函数。非法长度、JSON 或字段导致 `ERROR` 后关闭当前连接。其他连接继续服务。连接建立后 5 秒内必须发送首条消息（可配置）。

公共字段：`version` 固定整数 1；`type` 为已定义字符串；`payload` 必须为对象；`device_id` 和 `request_id` 存在时必须为 1—128 字符的非空字符串。额外字段目前不解释；接收方仍需验证对应业务载荷。JSON 不允许 NaN 或 Infinity。

## 已实现消息

| 消息 | 方向与载荷 |
|---|---|
| REGISTER | 设备 → 网关；需要 device_id，payload 为 device_type=light 与 state.power 布尔值 |
| REGISTERED | 网关 → 设备；带 device_id，空 payload |
| LIST_DEVICES | CLI → 网关；空 payload，短连接 |
| DEVICES | 网关 → CLI；payload.devices 为在线设备数组 |
| COMMAND | CLI → 网关、网关 → 设备；device_id，payload.action=set_power、value 布尔值 |
| COMMAND_RESULT | 设备 → 网关、网关 → CLI；device_id、request_id，payload 包含 status 和成功时的 state |
| ERROR | 网关 → 发送方；payload.message 为错误说明 |

网关转发指令时生成 `request_id`；不信任 CLI 指定的编号。设备按收到的编号原样回复。网关把结果与原设备会话绑定，其他设备不能代为确认。

设备结果的 `status` 仅接受 `success` 或 `failed`，两者均要求 `state.power` 布尔值。网关给 CLI 的结果另可为 `unknown`，并带 `reason`。当前 reason 包括 `device_offline`、`device_busy`、`confirmation_timeout` 和 `device_disconnected`。

## 例子

注册：

```json
{"version":1,"type":"REGISTER","device_id":"light_001","payload":{"device_type":"light","state":{"power":false}}}
```

网关给设备的指令：

```json
{"version":1,"type":"COMMAND","device_id":"light_001","request_id":"example_command_1","payload":{"action":"set_power","value":true}}
```

设备结果：

```json
{"version":1,"type":"COMMAND_RESULT","device_id":"light_001","request_id":"example_command_1","payload":{"status":"success","state":{"power":true}}}
```

JSON 示例本身不带长度头，不能直接作为 TCP 帧发送。

## 已预留但未完成的功能

已识别 `HEARTBEAT` 和 `REPORT`：注册设备发送时须携带一致的 device_id；REPORT 当前接受 state.power 并更新快照。网关记录活跃时间并发布事件，但灯示例尚不定时发送、网关也不做心跳过期处理。

UDP 还没有监听器。温湿度载荷、设备会话编号、递增序号、门锁动作以及重试去重规则需要在组员 1/2 开发前共同补充到此文档。不要把这些预留内容当成已经能使用的 API。
