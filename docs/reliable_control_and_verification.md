# 设备状态管理与可靠控制模块说明及异常场景验证记录

负责人：金欣怡（组员 B：状态与可靠控制）
所属分支：verify/jinxinyi-reliable-control（基于 integration）


## 一、 集成后代码复核结论（心跳与迟到回复处理）

1. 心跳超时断连与重连恢复（复核通过）
   - 软离线与断连：在 gateway/registry.py 的 mark_offline() 中，设备心跳超时被置为 online=False 时，会同步调用 self.writer.close() 关闭失效的 TCP 连接，保留最后已知状态 (state) 与 last_seen，并触发设备端自动重连。
   - 过期连接拦截：在 gateway/server.py 的 _read_device 中，增加了 session.online 及当前会话归属校验，直接拦截过期半开连接的报文并要求重新注册。
   - 排队锁继承：在 register() 中重连会话自动继承 existing.command_lock，保证重连期间排队指令的串行安全。

2. 迟到回复（Stale ACK）防状态覆盖（复核通过）
   - 在 _read_device 处理 COMMAND_RESULT 时，优先检查 self._pending.get(request_id)。
   - 若该请求已超时结案或不属于当前会话，直接跳过丢弃报文，不执行下方的 session.touch(state)，确保迟到回复不会覆盖设备的最新状态。


## 二、 可靠控制流程说明

1. 前置校验与快速失败
   - submit_command 校验设备类型与动作匹配（light 对应 set_power，door_lock 对应 set_locked）。
   - 若设备未注册或处于软离线（online=False），立即返回 {"status": "failed", "reason": "device_offline"}。

2. 同设备 FIFO 串行排队
   - 通过 async with session.command_lock 保证同一设备的并发控制指令严格按先后顺序排队执行，并在获得锁后二次校验在线状态。

3. 唯一 request_id 与有限超时重试
   - 每条指令分配唯一 request_id，在首次发送及最多 2 次超时重发（max_retries = 2，共最多 3 次尝试）中保持 request_id 不变。
   - 配合模拟设备端（组员 A）的 request_id 缓存机制实现幂等去重：设备收到重复 request_id 时仅重放上次执行结果，不重复执行开关动作。
   - 单次等待使用 asyncio.wait_for(asyncio.shield(future), command_timeout)；若 3 次尝试均超时，清理 _pending 并返回 {"status": "unknown", "reason": "confirmation_timeout"}。


## 三、 异常场景测试步骤与验证结果

验证命令：python -m unittest discover -s tests（全量 46 项测试通过）

【场景 1】同设备多条命令排队
- 触发方式：对同一设备连续并发调用 submit_command（对应 test_member2.py）
- 预期结果：不报忙拒绝，按 FIFO 顺序依次发送并确认
- 实际结果：通过。两条指令依次返回 success，状态按序更新。

【场景 2】首次确认丢失与超时有限重试
- 触发方式：第 1 次下发后模拟丢包不回，等待超时触发第 2 次重发后回包（对应 test_member2.py）
- 预期结果：触发 command.timeout_retry，重发相同 request_id 并执行成功
- 实际结果：通过。attempt=2 收到确认，最终返回 status: success。

【场景 3】重复命令幂等去重（与组员 A 联调）
- 触发方式：向灯 (light_001) 和门锁 (lock_001) 重复发送相同 request_id（对应 test_devices.py）
- 预期结果：设备识别重复 ID，重放历史 COMMAND_RESULT，不重复翻转硬件状态
- 实际结果：通过。控制台输出 duplicate request_id req_a; replaying earlier result without executing。

【场景 4】设备无响应与重试耗尽返回未知
- 触发方式：设备保持连接但不回复任何 COMMAND_RESULT（对应 test_member2.py）
- 预期结果：首发 1 次 + 重试 2 次耗尽后返回 unknown
- 实际结果：通过。返回 {"status": "unknown", "reason": "confirmation_timeout"}。

【场景 5】心跳超时软离线与重连恢复
- 触发方式：触发 expire_inactive 使门锁离线，等待门锁自动重连后再发控制指令（对应 test_integration.py）
- 预期结果：离线期间保留状态；重连后恢复 online=True 且控制成功
- 实际结果：通过。门锁自动重连注册后，set_locked 控制返回 success。

【场景 6】迟到回复（Stale ACK）过滤
- 触发方式：先开启(True)再关闭(False)，随后注入第 1 次开启指令的迟到回复（对应 test_integration.py）
- 预期结果：迟到回复被丢弃，状态保持 False 不被覆盖
- 实际结果：通过。设备状态保持 {"power": False}。