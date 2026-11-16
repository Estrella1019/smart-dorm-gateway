# 组员 C：网页控制台交付（11月16日）

## 已完成与边界

integration 已合并 TCP 灯/门锁、UDP 温湿度、可靠控制和网页。网页提供 HTTP 查询/控制、WebSocket 实时快照、断线后2秒重连、执行中/成功/失败/结果未知提示。页面将 power、locked、temperature、humidity 转成中文状态和单位。状态只依据网关快照，不因点击按钮自行改成成功。

组员C使用 Python aiohttp、asyncio、HTML/CSS/JavaScript 和 Fetch/WebSocket。create_app(gateway) 注入现有 Gateway；GET /api/devices 调 list_devices，POST /api/devices/{id}/commands 调 await submit_command，JSON 为 action/value，动作 set_power 或 set_locked，value 必须布尔。400 表示参数错误，422 表示网关拒绝动作，HTTP 200 内还须检查 status。GET /ws 首次和每次事件返回 event/devices。每浏览器队列32条，满时丢最旧状态消息；不承诺完整日志。

## 启动

仓库根目录运行 python -m pip install -r requirements.txt，再运行 python -m web.server。该入口已启动网关，不要另开 gateway.server。浏览器访问 http://127.0.0.1:8080 。另开终端分别运行 python -m devices.light、python -m devices.door_lock、python -m devices.sensor。配置和异常注入参数以各命令 --help 和 docs/integration.md 为准。

## 人工验收表（尚未执行，不是通过记录）

|步骤|应观察结果|实际结果|
|---|---|---|
|打开页面并启动三种设备|列表出现灯、门锁、传感器|待验收|
|开灯、关灯|执行中后成功，设备终端与页面一致|待验收|
|上锁、解锁|终端与中文状态一致|待验收|
|观察传感器|温度℃和湿度%随UDP上报更新|待验收|
|停止并重新启动设备|离线、恢复在线，状态正确|待验收|
|停止并重启网页服务|连接中断禁用按钮，恢复后刷新快照|待验收|
|按设备支持的参数延迟或丢弃回复|结果未知不等于未执行；记录重试|待联合验收|

验收时记录日期、浏览器版本、操作、预期/实际和截图。没有人工操作和截图时不能填写通过。自动测试不代替浏览器验收。

## 后续依赖

门锁与传感器的原网关缺口已在 integration 补齐，不再是阻塞。组员A/B配合异常注入和设备恢复验证，组员D补日志持久化及查询；日志接口未就绪时网页不虚构查询结果。组员C还需完成浏览器人工验收及演示录像，11月19日前提交实际记录。不要修改其他组员Socket实现。

## 讲解和教授问答

数据流：按钮 → HTTP → submit_command → 原TCP设备 → COMMAND_RESULT → HTTP结果；状态变化 → EventBus → 有界队列 → WebSocket → 页面。UDP传感器先进入网关，再走相同状态展示链路。

问：TCP可靠为何还要确认？答：TCP保证字节流，不保证设备执行，须应用层结果确认。
问：超时为何未知？答：设备可能执行了但确认丢失，不能直接认定失败。
问：谁负责重试去重？答：网关和设备；网页不自行重发控制，避免产生新请求号重复操作。
问：HTTP与WebSocket区别？答：HTTP查询提交，WebSocket主动推送；重连重新取快照。
问：两种心跳？答：WebSocket维护浏览器连接，网关设备心跳维护设备在线状态。
问：队列满怎么办？答：丢旧状态，较新消息包含完整快照；审计日志交给日志模块。
问：有数据库和鉴权吗？答：网页模块没有，默认回环地址用于课程模拟环境。

## 本次验证记录

2026-11-16，本机完整回归46项全部通过，包含门锁和UDP网页集成测试。JavaScript语法检查通过。测试期间出现UDP socket/transport未关闭的ResourceWarning，未修改设备与网关代码，交组员A/集成负责人跟进。浏览器人工验收未执行，以上表格保持待验收。
