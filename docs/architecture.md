# 架构与接口

## 当前可运行流程

```text
client.cli ── TCP 一次请求 ── Gateway.submit_command
                                     │
                       请求编号 + 待确认 Future
                                     │
                              模拟灯长连接
                                     │
                            COMMAND_RESULT
                                     │
                     更新状态 → 事件 → CLI 结果
```

网关用一个异步读取循环处理每台设备。CLI 请求用单独连接，不会与设备端同时读取同一 StreamReader。四字节长度前缀和 `readexactly` 处理分包、连续多包；完整帧一次写入，然后 `drain`。

## 组长维护的公共部分

- `shared/protocol.py`：`make_message`、`encode_message`、`read_message`、`send_message`。
- `shared/config.py`：配置字段及校验。新增字段要同时更新样例和文档。
- `gateway/server.py`：`Gateway.start/close`、TCP 接入与设备消息分发。
- `gateway/registry.py`：`DeviceSession` 和内存注册表。状态管理由组员 2 接手扩展。

目前重复在线编号被拒绝，不替换旧连接。清理时仍检查会话对象身份，避免未来切换重连策略后误删新会话。

## 给组员的调用接口

### 设备发送

`await gateway.send_to_device(device_id, message)`：发送完整协议消息；设备不在线时抛出 `ConnectionError`。调用者仍需处理传输期间的断线。它只负责发送，不等于设备执行成功。

### 控制服务

`gateway.list_devices()`：返回快照列表。

`await gateway.submit_command(device_id, action, value)`：当前仅支持 `set_power` 与布尔值。返回示例：

```json
{"request_id":"网关生成的编号","status":"success","state":{"power":true}}
```

允许状态：`success`、`failed`、`unknown`。不存在设备和忙状态会明确失败；发送期间断线或确认超时的结果是未知。现有函数等待最终结果，组员 3 初期可直接调用；若组员 2 改成“受理后立即返回”，需要联合更新接口及测试，不能默默改变语义。抽象契约见 `gateway/services.py`。

### 事件订阅

`gateway.events.subscribe(callback)`；回调收到含 `event` 和 UTC `timestamp` 的字典。

事件包括 `device.connected`、`device.disconnected`、`device.state`、`device.heartbeat`、`command.submitted`、`command.completed`。相关事件带 `device_id`，指令事件带 `request_id`。回调目前同步执行，必须快速返回；组员 3/4 应放入有上限的队列后异步推送或写数据库，不能在回调里阻塞网关。

该事件通道不是持久消息队列，不提供重放或投递保证。接收方不得修改事件对象。

## 后续模块边界

- 组员 1：扩展 `devices/`，使用公共编解码，不复制协议实现。
- 组员 2：将控制状态机从 `Gateway` 抽到独立服务，保留服务契约；补离线记录、心跳检测、重试与排队。
- 组员 3：增加 HTTP/WebSocket 适配器与 `web/` 页面，调用控制服务、订阅事件，不直接写设备 Socket。
- 组员 4：新增日志存储和测试工具，使用事件通道；日志查询函数由组员 3 包装成 HTTP 接口。
- 组长：补 UDP 接入层，与组员 1 约定传感器数据和会话序号规则。

## 当前可靠性边界

没有心跳超时检测，半开连接不会主动标记离线。没有重试、去重或状态持久化。超时确认不会把状态强行写成“已执行”；迟到的结果会忽略，后续 `REPORT` 可以核对状态。灯进程重启后恢复为关闭。当前最大连接数和写超时只是基础保护，不是生产级流控。
