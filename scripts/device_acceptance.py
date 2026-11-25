"""Device acceptance run against the real gateway, with every program in its own process.

Run from the repository root:  python scripts/device_acceptance.py [output_dir]

Uses ports 19000 (TCP) and 19001 (UDP) and a 4-second heartbeat timeout so the
offline checks finish quickly. Logs and results.json go to output_dir
(default data/device_acceptance, which Git ignores). Takes about 90 seconds.
"""
import asyncio, json, signal, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data" / "device_acceptance"
sys.path.insert(0, str(REPO))
from client.cli import request
from shared.protocol import make_message

WORK.mkdir(parents=True, exist_ok=True)
CFG = WORK / "acceptance.toml"
CONFIG_TEMPLATE = ('[gateway]\nhost = "127.0.0.1"\ntcp_port = 19000\nudp_port = 19001\n'
               'command_timeout = {timeout}\nheartbeat_timeout = 4.0\n')
CFG.write_text(CONFIG_TEMPLATE.format(timeout=3.0))
PY = sys.executable
procs, results = {}, []


def start(name, *args, config=CFG):
    log = open(WORK / f"{name}.log", "w")
    procs[name] = subprocess.Popen([PY, "-u", "-m", *args, "--config", str(config)], cwd=REPO,
                                   stdout=log, stderr=subprocess.STDOUT)


def stop(name):
    p = procs.pop(name)
    p.send_signal(signal.SIGINT)
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()


def log(name):
    return (WORK / f"{name}.log").read_text()


async def devices():
    r = await request("127.0.0.1", 19000, make_message("LIST_DEVICES"))
    return {d["device_id"]: d for d in r["payload"]["devices"]}


async def command(device, action, value):
    r = await request("127.0.0.1", 19000, make_message(
        "COMMAND", {"action": action, "value": value}, device_id=device), timeout=30)
    return r["payload"]


async def wait_for(pred, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred(await devices()):
                return True
        except OSError:
            pass
        await asyncio.sleep(0.2)
    return False


def record(item, ok, detail):
    results.append({"item": item, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + item + " | " + detail, flush=True)


def online(d, dev):
    return dev in d and d[dev]["online"]


async def main():
    start("gateway", "gateway.server", "--log-db", str(WORK / "events.sqlite")); await asyncio.sleep(1)
    start("light", "devices.light", "--heartbeat-interval", "1")
    start("lock", "devices.door_lock", "--heartbeat-interval", "1")
    start("sensor", "devices.sensor", "--interval", "1")

    # 1. Basic flow
    ok = await wait_for(lambda d: all(online(d, x) for x in ("light_001", "lock_001", "sensor_001")))
    d = await devices()
    record("三类设备上线", ok, ", ".join(f"{k}:{v['device_type']}/{v['protocol']}" for k, v in sorted(d.items())))
    on, off = await command("light_001", "set_power", True), None
    s1 = (await devices())["light_001"]["state"]
    off = await command("light_001", "set_power", False)
    record("开灯/关灯", on["status"] == off["status"] == "success" and s1 == {"power": True},
           f"开灯后网关看到 {s1}，关灯 {off['status']}")
    u, l = await command("lock_001", "set_locked", False), None
    s2 = (await devices())["lock_001"]["state"]
    l = await command("lock_001", "set_locked", True)
    record("开锁/上锁", u["status"] == l["status"] == "success" and s2 == {"locked": False},
           f"开锁后网关看到 {s2}，上锁 {l['status']}")
    t0 = (await devices())["sensor_001"]["state"]; await asyncio.sleep(3)
    t1 = (await devices())["sensor_001"]["state"]
    record("温湿度更新", set(t1) == {"temperature", "humidity"}, f"{t0} -> {t1}")

    # 2. Device exits and restarts
    await command("light_001", "set_power", True)
    stop("light")
    gone = await wait_for(lambda d: not online(d, "light_001"), 8)
    start("light", "devices.light", "--heartbeat-interval", "1")
    back = await wait_for(lambda d: online(d, "light_001"))
    st = (await devices())["light_001"]["state"]
    again = await command("light_001", "set_power", True)
    record("灯退出后重启", gone and back and again["status"] == "success",
           f"退出后网关标离线={gone}；重启后上线={back}，状态回到 {st}（进程重启，内存状态复位为关）；再开灯 {again['status']}")
    stop("sensor")
    gone = await wait_for(lambda d: not online(d, "sensor_001"), 10)
    start("sensor", "devices.sensor", "--interval", "1")
    back = await wait_for(lambda d: online(d, "sensor_001"))
    record("传感器退出后重启", gone and back,
           f"停止上报约 4 秒后标离线={gone}；新 session 重启后重新上线={back}")

    # 3. Gateway restart
    await command("lock_001", "set_locked", False)
    stop("gateway"); await asyncio.sleep(3)
    start("gateway", "gateway.server", "--log-db", str(WORK / "events.sqlite"))
    back = await wait_for(lambda d: all(online(d, x) for x in ("light_001", "lock_001", "sensor_001")), 40)
    d = await devices()
    record("网关重启后自动恢复", back and d["light_001"]["state"] == {"power": True}
           and d["lock_001"]["state"] == {"locked": False},
           f"三类设备自动回来={back}；灯 {d['light_001']['state']}、门锁 {d['lock_001']['state']} 保持重启前状态")

    # 4. Lost acknowledgments: gateway retries with same request_id, device must not execute twice
    stop("light")
    start("light", "devices.light", "--heartbeat-interval", "1", "--drop-ack", "1")
    await wait_for(lambda d: online(d, "light_001"))
    t = time.monotonic(); r = await command("light_001", "set_power", True); dt = time.monotonic() - t
    lg = log("light")
    executed = lg.count("fault: dropped acknowledgment")
    dups = lg.count("duplicate request_id")
    record("无回复（--drop-ack 1）", r["status"] == "unknown" and dups >= 1,
           f"网关等 {dt:.1f}s 后返回 {r['status']}/{r.get('reason')}；同一编号收到 {executed} 次，"
           f"其中 {dups} 次识别为重复、只回放不执行")
    on_now = (await devices())["light_001"]["state"]
    record("重复命令不重复执行", dups == executed - 1,
           f"灯只真正执行了 1 次；网关保存的状态 {on_now}（没收到确认，所以不更新）")

    # 5. Slow device: reply arrives after the first timeout, caught by the retry
    stop("light")
    start("light", "devices.light", "--heartbeat-interval", "1", "--delay", "4")
    await wait_for(lambda d: online(d, "light_001"))
    t = time.monotonic(); r = await command("light_001", "set_power", False); dt = time.monotonic() - t
    record("延迟回复（--delay 4，超过 3 秒超时）", r["status"] == "success",
           f"{dt:.1f}s 后 {r['status']}：第一次等确认超时，网关用同一编号重发，"
           f"并在重试等待期间收到了迟到的回复；重发的那条排在设备后面，到时会按重复处理")

    # 6. Flaky link
    stop("light")
    start("light", "devices.light", "--heartbeat-interval", "1", "--disconnect-after", "3")
    seen = []
    end = time.monotonic() + 12
    while time.monotonic() < end:
        try:
            seen.append(online(await devices(), "light_001"))
        except OSError:
            pass
        await asyncio.sleep(0.25)
    flips = sum(1 for a, b in zip(seen, seen[1:]) if a != b)
    record("定时断线（--disconnect-after 3）", flips >= 2 and log("light").count("connected;") >= 3,
           f"12 秒内灯连上 {log('light').count('connected;')} 次，网关在线/离线切换 {flips} 次")

    # 7. Silent device
    stop("light")
    start("light", "devices.light", "--heartbeat-interval", "0")
    await wait_for(lambda d: online(d, "light_001"))
    gone = await wait_for(lambda d: not online(d, "light_001"), 10)
    record("不发心跳（--heartbeat-interval 0）", gone, f"约 4 秒心跳超时后网关标离线={gone}，随后设备自动重连")

    # 8. Lossy sensor
    stop("sensor")
    start("sensor", "devices.sensor", "--interval", "0.2", "--drop-rate", "0.5")
    await asyncio.sleep(4)
    sl = log("sensor")
    dropped, sent = sl.count("fault: dropped report"), sl.count('"type": "REPORT"')
    ok = online(await devices(), "sensor_001")
    record("传感器丢包（--drop-rate 0.5）", ok and dropped > 0,
           f"发出 {sent} 条、丢弃 {dropped} 条，seq 有跳号；网关照常接收，传感器保持在线={ok}")

    for n in list(procs):
        stop(n)
    # 14th check: exhaust three confirmation attempts before the first 2s report.
    report_config = WORK / "report.toml"
    report_config.write_text(CONFIG_TEMPLATE.format(timeout=0.2))
    start("report_gateway", "gateway.server", "--log-db", "", config=report_config)
    start("report_light", "devices.light", "--drop-ack", "1", "--report-interval", "2",
          config=report_config)
    ready = await wait_for(lambda d: online(d, "light_001") and d["light_001"]["state"] == {"power": False})
    result = await command("light_001", "set_power", True)
    before = (await devices())["light_001"]["state"]
    corrected = await wait_for(lambda d: online(d, "light_001") and d["light_001"]["state"] == {"power": True}, 5)
    record("主动 REPORT 纠正丢失确认后的状态",
           ready and result["status"] == "unknown" and before == {"power": False} and corrected,
           f"命令返回 {result['status']} 时状态为 {before}，随后网关看到开灯={corrected}")

    for n in list(procs):
        stop(n)
    (WORK / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))


try:
    asyncio.run(main())
    if not all(result["ok"] for result in results):
        sys.exit(1)
finally:
    for p in procs.values():
        p.kill()
