#!/usr/bin/env bash
# Container Alert v1.6.0: real Docker engine smoke tests, without host installations.
# Run on an authorized lab admin client with docker CLI, Python 3, a local DFC syslog receiver.
# Creates only clearly labelled disposable containers and Docker named volumes.
set -euo pipefail
exec python3 - "$@" <<'PY'
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback

IMAGE = os.environ.get("SMOKE_IMAGE", "watchdog:1.6.0")
EXPECTED_REV = os.environ.get("SMOKE_EXPECTED_REV", "")
TAG = dt.datetime.now(dt.timezone.utc).strftime("%y%m%d%H%M%S") + str(os.getpid())[-3:]
PREFIX = "ca-smoke-" + TAG
LABEL = "container-alert.smoke=" + TAG
ISOLATED = os.environ.get("SMOKE_ISOLATED", "") == "1"
RECEIVER = PREFIX + "-syslog-receiver" if ISOLATED else "config_dfc_1"
HOST = "release-smoke-" + TAG
OBS = PREFIX + "-observer"
STATE_VOL = PREFIX + "-state"
BASE = pathlib.Path(os.environ.get("SMOKE_OUTPUT_DIR", "/tmp")) / ("container-alert-smoke-" + TAG)
START = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
containers, volumes, results, facts = [], [], [], {}
EVENT_PROC, EVENT_FILE = None, None
BASE.mkdir(parents=True, exist_ok=False)
start_mono = time.monotonic()

def arm_events():
    # Capture live to avoid Docker's short retrospective event-history buffer.
    global EVENT_PROC, EVENT_FILE
    EVENT_FILE = (BASE/"docker-events-live.jsonl").open("w")
    EVENT_PROC = subprocess.Popen(
        ["docker", "events", "--format", "{{json .}}", "--filter", "type=container",
         "--filter", "label=" + LABEL],
        stdout=EVENT_FILE, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    ensure(EVENT_PROC.poll() is None, "real-time docker events capture failed")

def stop_events():
    global EVENT_PROC, EVENT_FILE
    if EVENT_PROC is not None:
        EVENT_PROC.terminate()
        try:
            EVENT_PROC.wait(timeout=4)
        except subprocess.TimeoutExpired:
            EVENT_PROC.kill()
            EVENT_PROC.wait(timeout=4)
        EVENT_PROC = None
    if EVENT_FILE is not None:
        EVENT_FILE.close()
        EVENT_FILE = None
    p = BASE/"docker-events-live.jsonl"
    if p.exists():
        events = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        facts["live_docker_events"] = len(events)
        facts["live_docker_action_counts"] = {
            a: sum(e.get("Action") == a for e in events)
            for a in ("create","start","die","oom","stop","kill","restart","health_status: unhealthy",
                      "health_status: healthy","destroy")
        }

def run(argv, timeout=30, check=True):
    args = ["docker"] + [str(v) for v in argv]
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("docker " + " ".join(args[1:5]) +
                           " returned " + str(p.returncode) + ": " + (p.stderr or p.stdout)[-1500:])
    return p.stdout.strip()

def log(s):
    print(s, flush=True)

def mark(name, expected, evidence):
    evidence = str(evidence)
    elapsed = round(time.monotonic() - start_mono, 1)
    results.append({"id": name, "status": "PASS", "expected": expected,
                    "observed": evidence, "elapsed_seconds": elapsed})
    log("PASS " + name + " - " + evidence[:190])

def issue(name, expected, err):
    elapsed = round(time.monotonic() - start_mono, 1)
    results.append({"id": name, "status": "FAIL", "expected": expected,
                    "observed": str(err), "elapsed_seconds": elapsed})
    log("FAIL " + name + " - " + str(err)[:350])

def checked(name, expected, fn):
    try:
        mark(name, expected, fn())
        return True
    except Exception as exc:
        issue(name, expected, exc)
        return False

def ensure(ok, message):
    if not ok:
        raise AssertionError(message)

def fixture(suffix, program, options=(), memory="128m", control=None):
    name = PREFIX + "-" + suffix
    args = ["create", "--name", name, "--network", "none", "--label", LABEL,
            # The image was built by Compose and carries service labels; do not
            # let the lab reconciliation loop mistake test fixtures for watchdogs.
            "--label", "com.docker.compose.project=release-smoke",
            "--label", "com.docker.compose.service=fixture",
            "--restart", "no",
            "--cpus", "0.20", "--memory", memory, "--memory-swap", memory,
            "--pids-limit", "64", "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL"]
    if control:
        args += ["--mount", "type=volume,source=" + control + ",target=/control"]
    args += list(options) + ["--entrypoint", "python3", IMAGE, "-u", "-c", program]
    cid = run(args)
    containers.append(name)
    facts[suffix] = {"name": name, "container_id": cid}
    return name

def start(name):
    run(["start", name])

def inspect(name, value):
    return run(["inspect", "--format", value, name])

def state(name):
    return json.loads(inspect(name, "{{json .State}}"))

def wait_exit(name, limit=30):
    return int(run(["wait", name], timeout=limit).splitlines()[-1])

def count(name, kind):
    a = alerts(name, kind)
    return len(a)

def alerts(name, kind=None):
    raw = run(["logs", "--since", START, OBS], timeout=20, check=False)
    lines = [x for x in raw.splitlines() if 'event="watchdog_alert"' in x
             and ('container="' + name + '"') in x]
    if kind is not None:
        lines = [x for x in lines if ('failure_type="' + kind + '"') in x]
    return lines

def wait_alert(name, kind, n=1, limit=45):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if count(name, kind) >= n:
            return count(name, kind)
        time.sleep(1)
    raise AssertionError("timeout waiting for " + str(n) + " " + kind +
                         " alerts on " + name + "; observed=" + repr(alerts(name)[-3:]))

def must_count(name, kind, n):
    actual = count(name, kind)
    ensure(actual == n, name + " " + kind + ": expected " + str(n) + " got " + str(actual))
    return actual

def make_volume(suffix):
    name = PREFIX + "-" + suffix
    run(["volume", "create", "--label", LABEL, name])
    volumes.append(name)
    # The actual test process is UID 1000; test control files must be writable.
    if suffix != "state":
        run(["run", "--rm", "--network", "none", "--user", "0:0",
             "--volume", name + ":/control", "--entrypoint", "python3",
             IMAGE, "-c", "import os;os.chown('/control',1000,1000)"])
    return name

def write_control(volume, filename, present=True):
    code = ("import pathlib; p=pathlib.Path('/control')/" + repr(filename) +
            "; p.write_text('yes') if " + repr(present) + " else p.unlink(missing_ok=True)")
    run(["run", "--rm", "--network", "none", "--user", "0:0",
         "--volume", volume + ":/control", "--entrypoint", "python3",
         IMAGE, "-c", code], timeout=35)

def wait_restarts(name, minimum, limit=120):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        n = int(inspect(name, "{{.RestartCount}}"))
        if n >= minimum:
            return n
        time.sleep(2)
    raise AssertionError("restart count did not reach " + str(minimum))

def require_syslog():
    # Keep the isolated collector's raw received packets as separate evidence.
    if ISOLATED:
        data = run(["exec", RECEIVER, "cat", "/var/log/syslog"], timeout=20)
        (BASE/"syslog-udp-raw.txt").write_text(data+"\n")
    else:
        data = run(["exec", RECEIVER, "sh", "-c",
                    "grep -F " + "'host=" + '"' + HOST + '"' + "'" +
                    " /var/log/syslog || true"], timeout=20, check=False)
    return [x for x in data.splitlines()
            if 'host="' + HOST + '"' in x and 'event="watchdog_alert"' in x]

def init():
    if not ISOLATED:
        ensure(run(["inspect", "--format", "{{.State.Health.Status}}",
                    "docker-container-watchdog"]) == "healthy", "existing watchdog not healthy")
    revision = run(["image", "inspect", "--format",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}', IMAGE])
    if EXPECTED_REV:
        ensure(revision == EXPECTED_REV, "wrong image source revision " + revision)
    image_id = run(["image", "inspect", "--format", "{{.Id}}", IMAGE])
    embedded = run(["run", "--rm", "--network", "none", "--entrypoint", "sha256sum",
                    IMAGE, "/app/watchdog.py", "/app/lifecycle.py", "/app/ha_monitor.py"])
    expected_sha = {
        "/app/watchdog.py": os.environ.get(
            "SMOKE_EXPECTED_WATCHDOG_SHA256",
            "4df8451b496a2935eba17fd56cc87459c87b4f96d9c2ace5bee9161fdf2c60fa"),
        "/app/lifecycle.py": "55b6a06ebb69cb5a70f6c1566798087510228ef93eeceb810dcadb5da09d81f9",
        "/app/ha_monitor.py": "0fb3ba165035c9970f91854914709fd7492e21a7583c74f34174275c12083dbd",
    }
    actual_sha = {line.split()[-1]: line.split()[0] for line in embedded.splitlines()}
    ensure(actual_sha == expected_sha, "release image source SHA mismatch: " + repr(actual_sha))
    facts["embedded_source_sha256"] = actual_sha
    if not ISOLATED:
        ensure(RECEIVER in run(["ps", "--format", "{{.Names}}"]).splitlines(),
               "DFC syslog receiver not running")
    existing = run(["ps", "-a", "--format", "{{.Names}}"]).splitlines()
    ensure(not any(n.startswith(PREFIX) for n in existing), "smoke prefix collision")
    ensure(not run(["volume", "ls", "--filter", "label=" + LABEL, "-q"]), "old test volumes")
    if ISOLATED:
        collector = make_volume("syslogdata")
        udp_code = """import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind(("0.0.0.0",514))
with open("/var/log/syslog","a",buffering=1) as output:
    print("SMOKE_SYSLOG_RECEIVER_READY", flush=True)
    while True:
        packet,source=s.recvfrom(65535)
        output.write(packet.decode(errors="replace")+"\\n")
"""
        run(["run", "-d", "--name", RECEIVER, "--label", LABEL, "--network", "host",
             "--restart", "no", "--memory", "64m", "--pids-limit", "32",
             "--volume", collector + ":/var/log", "--user", "0:0",
             "--entrypoint", "python3", IMAGE, "-u", "-c", udp_code])
        containers.append(RECEIVER)
        receiver_logs = ""
        for _ in range(18):
            time.sleep(1)
            receiver_logs = run(["logs", RECEIVER], check=False)
            if "SMOKE_SYSLOG_RECEIVER_READY" in receiver_logs:
                break
            if inspect(RECEIVER, "{{.State.Status}}") == "exited":
                break
        ensure("SMOKE_SYSLOG_RECEIVER_READY" in receiver_logs,
               "isolated UDP syslog collector failed; state=" +
               inspect(RECEIVER, "{{.State.Status}}") + ", log=" + receiver_logs[-1000:])
    vol = make_volume("state")
    excludes = sorted(set(existing + [OBS]))
    config = {
        "alert_channels": [], "log_level": "INFO", "log_file": None,
        "check_interval_seconds": 2, "cooldown_minutes": 5,
        "restart_threshold": 5, "restart_window_minutes": 10,
        "restart_recovery_stability_seconds": 120, "unhealthy_cycles_threshold": 3,
        "alert_on_recovery": True, "boot_grace_seconds": 0,
        "lifecycle_detection": {"enabled": True, "correlation_window_seconds": 3},
        "ha_monitor": {"enabled": False},
        "auto_health_check": {"enabled": False},
        "excluded_containers": excludes,
        "state_file": "/var/log/watchdog/state.json",
        "syslog": {"enabled": True, "host": "127.0.0.1", "port": 514,
                   "protocol": "udp", "facility": "local0"}
    }
    setup = ("import os,json,pathlib; p=pathlib.Path('/data'); "
             "c=json.loads(os.environ['SMOKE_CONFIG']); "
             "(p/'watchdog-config.yaml').write_text(json.dumps(c)); "
             "(p/'watchdog-config.yaml').chmod(0o644); os.chown(p,1000,1000)")
    run(["run", "--rm", "--network", "none", "--user", "0:0",
         "-e", "SMOKE_CONFIG=" + json.dumps(config),
         "--volume", vol + ":/data", "--entrypoint", "python3",
         IMAGE, "-c", setup], timeout=30)
    gid = int(os.environ.get("SMOKE_DOCKER_SOCKET_GID", os.stat("/var/run/docker.sock").st_gid))
    arm_events()
    args = ["run", "-d", "--name", OBS, "--label", LABEL, "--restart", "no",
            "--label", "com.docker.compose.project=release-smoke",
            "--label", "com.docker.compose.service=observer",
            "--network", "host", "--cpus", "0.35", "--memory", "256m",
            "--pids-limit", "96", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "--user", "1000:1000", "--group-add", str(gid),
            "--log-driver", "local", "--log-opt", "max-size=5m",
            "--log-opt", "max-file=2",
            "-e", "WATCHDOG_HOST=" + HOST,
            "-e", "WATCHDOG_CONFIG=/etc/watchdog/watchdog-config.yaml",
            "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
            "-v", vol + ":/etc/watchdog:ro",
            "-v", vol + ":/var/log/watchdog:rw",
            IMAGE]
    run(args)
    containers.append(OBS)
    time.sleep(3)
    lines = run(["logs", OBS])
    ensure("Docker event listener started" in lines and "Poll loop started" in lines,
           "observer did not initialize: " + lines[-700:])
    ensure(inspect(OBS, "{{.State.Status}}") == "running", "observer stopped")
    facts.update({"host": run(["info", "--format", "{{.Name}}"]),
                  "revision": revision, "image_id": image_id, "observer": OBS,
                  "syslog_source_host": HOST, "observer_poll_seconds": 2,
                  "restart_threshold": 5, "recovery_stability_seconds": 120,
                  "excluded_existing_containers": len(existing),
                  "syslog_backend": "isolated UDP test collector" if ISOLATED else "real DFC",
                  "docker_engine_endpoint": os.environ.get("DOCKER_HOST", "default")})
    log("REAL_DOCKER_TEST_READY " + HOST + " image=" + image_id[:24])

def t01_clean():
    name = fixture("clean", "import sys;sys.exit(0)")
    start(name)
    ensure(wait_exit(name) == 0, "clean exit not 0")
    time.sleep(7)
    ensure(not alerts(name), "unexpected alert on clean exit")
    return "real exit=0, no alerts"

def t02_crash():
    name = fixture("crash", "raise SystemExit(2)")
    start(name)
    ensure(wait_exit(name) == 2, "unexpected exit code")
    wait_alert(name, "crashed", limit=25)
    ensure(must_count(name, "crashed", 1) == 1, "duplicate crash")
    ensure('severity="CRITICAL"' in alerts(name, "crashed")[0], "wrong severity")
    return "Docker exit=2, exactly one CRITICAL crashed alert"

def t03_planned_stop():
    name = fixture("planned", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(300)")
    start(name)
    time.sleep(2)
    run(["stop", "-t", "1", name], timeout=20)
    ensure(wait_exit(name, 20) == 137, "planned stop not exit=137")
    ensure(state(name)["OOMKilled"] is False, "planned stop unexpectedly OOM")
    time.sleep(7)
    ensure(not alerts(name), "planned stop misclassified: " + str(alerts(name)))
    ensure(state(name)["Status"] == "exited", "unexpected planned fixture restart")
    # The HA lab has an external restart manager; remove the finished fixture
    # before that independent service can start a new Docker run.
    run(["rm", "-f", name])
    return "real docker stop -> SIGKILL/137; no crash or OOM alert"

def t04_direct_kill():
    name = fixture("sigkill", "import time;time.sleep(300)")
    start(name)
    time.sleep(1)
    run(["kill", "--signal", "SIGKILL", name])
    ensure(wait_exit(name) == 137, "SIGKILL exit not 137")
    ensure(state(name)["OOMKilled"] is False, "SIGKILL incorrectly marked OOM")
    wait_alert(name, "crashed", limit=25)
    ensure(must_count(name, "crashed", 1) == 1, "duplicate crash")
    ensure(must_count(name, "oom", 0) == 0, "false OOM")
    facts["sigkill_state"] = {"exit": 137, "OOMKilled": False}
    run(["rm", "-f", name])
    time.sleep(12)
    ensure(must_count(name, "recovered", 0) == 0, "false recovery after removed SIGKILL")
    ensure(must_count(name, "crashed", 1) == 1, "repeated crash")
    return "real SIGKILL/137, OOMKilled=false, exactly one CRITICAL crash"

def t05_oom():
    name = fixture("oom", "import time;x=[]\nwhile True:\n x.append(bytearray(8*1024*1024));time.sleep(.02)",
                   memory="64m")
    start(name)
    ensure(wait_exit(name, limit=35) == 137, "OOM did not exit 137")
    ensure(state(name)["OOMKilled"] is True, "Docker cgroup did not confirm OOMKilled")
    wait_alert(name, "oom", limit=30)
    facts["oom_state"] = {"exit": 137, "OOMKilled": True}
    run(["rm", "-f", name])
    time.sleep(12)
    ensure(must_count(name, "recovered", 0) == 0, "false recovery after removed OOM")
    ensure(must_count(name, "oom", 1) == 1, "duplicate OOM")
    ensure(must_count(name, "crashed", 0) == 0, "false crash after confirmed OOM")
    line = alerts(name, "oom")[0]
    ensure('severity="CRITICAL"' in line, "incorrect OOM severity")
    return "kernel cgroup OOMKilled=true, exit=137, one CRITICAL OOM, zero crash"

def t06_t08_loop():
    vol = make_volume("loopcontrol")
    program = """import os,sys,time
if not os.path.exists('/control/recover'):
    time.sleep(0.7)
    sys.exit(2)
while True:
    if os.path.exists('/control/interrupt'):
        try: os.unlink('/control/interrupt')
        except OSError: pass
        sys.exit(2)
    time.sleep(0.4)
"""
    name = fixture("loop", program, options=("--restart", "always"), control=vol)
    start(name)
    wait_alert(name, "crashed", limit=50)
    wait_alert(name, "restart-loop", limit=135)
    n = int(inspect(name, "{{.RestartCount}}"))
    ensure(n >= 5, "loop threshold crossed without 5 real Docker restarts, saw " + str(n))
    ensure(must_count(name, "crashed", 1) == 1, "repeated crashes")
    ensure(must_count(name, "restart-loop", 1) == 1, "repeat loop")
    mark("T06_RESTART_LOOP", "one CRASH, one HIGH RESTART-LOOP at >=5 Docker restarts",
         "real Docker RestartCount=" + str(n) + "; crash=1; restart-loop=1")
    write_control(vol, "recover")
    running_at = time.monotonic()
    time.sleep(11)
    ensure(inspect(name, "{{.State.Status}}") == "running", "not recovering")
    ensure(must_count(name, "recovered", 0) == 0, "premature recovered after 11s")
    mark("T07_BRIEF_RECOVERY", "no recovery before 120s stability", "running after 11s, RECOVERED=0")
    # A new real process failure during the stability period resets the recovery clock.
    before = int(inspect(name, "{{.RestartCount}}"))
    write_control(vol, "interrupt")
    after = wait_restarts(name, before + 1, limit=45)
    time.sleep(14)
    ensure(must_count(name, "recovered", 0) == 0,
           "premature recovered 14s after interrupted recovery")
    mark("T08_INTERRUPTED_RECOVERY", "new actual die/reset prevents premature recovery",
         "Docker RestartCount " + str(before) + " -> " + str(after) +
         "; RECOVERED=0 after 14s")
    wait_alert(name, "recovered", limit=165)
    time.sleep(6)
    ensure(must_count(name, "crashed", 1) == 1, "crash storm")
    ensure(must_count(name, "restart-loop", 1) == 1, "loop repeated")
    ensure(must_count(name, "recovered", 1) == 1, "recovered not exactly once")
    ensure('severity="INFO"' in alerts(name, "recovered")[0], "recovery level not INFO")
    mark("T09_STABLE_RECOVERY", "one INFO RECOVERED after 120s sustained uptime",
         "1 crashed, 1 restart-loop, 1 recovered after interrupted recovery")
    facts["loop_restart_count_after"] = int(inspect(name, "{{.RestartCount}}"))
    return "one CRASH + one LOOP + one RECOVERED after real interrupted recovery"

def t14_external_restart():
    # A Docker policy=no container is intentionally started by an external actor.
    # RECOVERED must wait for sustained uptime, just like auto-restart containers.
    vol = make_volume("externalcontrol")
    script = """import os,sys,time
if not os.path.exists("/control/recover"): sys.exit(2)
time.sleep(300)
"""
    name = fixture("external", script, control=vol)
    start(name)
    ensure(wait_exit(name) == 2, "initial actual crash not exit 2")
    wait_alert(name, "crashed", limit=35)
    write_control(vol, "recover")
    ensure(inspect(name, "{{.HostConfig.RestartPolicy.Name}}") == "no",
           "external fixture should not auto-restart")
    run(["start", name])
    time.sleep(12)
    ensure(must_count(name, "recovered", 0) == 0,
           "premature RECOVERED after external Docker start, before 120s uptime")
    wait_alert(name, "recovered", limit=150)
    ensure(must_count(name, "crashed", 1) == 1, "duplicate crash")
    ensure(must_count(name, "recovered", 1) == 1, "duplicate recovery")
    stable = confirmed_stable_uptime(name)
    return "real manual Docker start after crash; RECOVERED after " + str(stable) + "s uptime"

def confirmed_stable_uptime(name):
    line = alerts(name, "recovered")[0]
    stamp = re.search(r'time_utc="([^"]+)"', line)
    ensure(stamp is not None, "RECOVERED alert missing timestamp")
    started = dt.datetime.fromisoformat(inspect(name, "{{.State.StartedAt}}").replace("Z","+00:00"))
    recovered = dt.datetime.fromisoformat(stamp.group(1).replace("Z","+00:00"))
    elapsed = (recovered-started).total_seconds()
    ensure(elapsed >= 119, "RECOVERED was emitted after only " + str(elapsed) + " seconds")
    return round(elapsed,1)

def t15_external_oom_restart():
    vol = make_volume("externaloomcontrol")
    script = """import os,time
if os.path.exists("/control/recover"):
    while True: time.sleep(2)
else:
    chunks=[]
    while True:
        chunks.append(bytearray(8*1024*1024))
        time.sleep(.02)
"""
    name = fixture("externaloom", script, memory="64m", control=vol)
    ensure(inspect(name, "{{.HostConfig.RestartPolicy.Name}}") == "no",
           "external OOM fixture must have restart policy no")
    start(name)
    ensure(wait_exit(name, 40) == 137, "kernel OOM did not exit 137")
    ensure(state(name)["OOMKilled"] is True, "Docker did not confirm real OOM")
    wait_alert(name, "oom", limit=35)
    ensure(must_count(name, "crashed", 0) == 0, "OOM produced CRASH")
    write_control(vol, "recover")
    run(["start", name])
    time.sleep(12)
    ensure(must_count(name, "recovered", 0) == 0, "early recovered after external OOM start")
    wait_alert(name, "recovered", limit=150)
    ensure(must_count(name, "oom", 1) == 1, "repeated OOM notification")
    ensure(must_count(name, "recovered", 1) == 1, "repeated RECOVERED")
    stable = confirmed_stable_uptime(name)
    return "real kernel OOM -> external Docker start -> INFO RECOVERED after " + str(stable) + "s"

def t10_name_reuse():
    name = PREFIX + "-reuse"
    ids = []
    for code in (2, 42):
        label = "reuse-" + str(code)
        cid = fixture("reuse", "raise SystemExit(" + str(code) + ")") if code == 2 else None
        if code != 2:
            # Reuse the exact same name after deleting the old container.
            ensure(not run(["ps", "-aq", "--filter", "name=^/" + name + "$"]),
                   "old ID still owns name")
            cid = fixture("reuse", "raise SystemExit(42)")
        ids.append(inspect(name, "{{.Id}}"))
        start(name)
        ensure(wait_exit(name) == code, "unexpected " + label + " exit")
        wait_alert(name, "crashed", n=len(ids), limit=30)
        if code == 2:
            run(["rm", name])
            time.sleep(2)
    ensure(ids[0] != ids[1], "same Docker instance reused")
    ensure(must_count(name, "crashed", 2) == 2, "new name inherited old alert")
    return "two unique container IDs, same name, each gets one CRITICAL crash"

def t11_fast_removal():
    names = []
    for k in range(3):
        name = PREFIX + "-rm" + str(k)
        run(["run", "--rm", "--name", name, "--label", LABEL,
             "--network", "none", "--memory", "128m", "--memory-swap", "128m",
             "--pids-limit", "64", "--cpus", "0.2", "--cap-drop", "ALL",
             "--entrypoint", "python3", IMAGE, "-c", "raise SystemExit(2)"],
            timeout=30, check=False)
        names.append(name)
    time.sleep(8)
    wd_logs = run(["logs", "--since", START, OBS], timeout=20)
    misses = [x for x in wd_logs.splitlines() if "already removed before" in x and
              any(n in x for n in names)]
    ensure(len(misses) <= 6, "missing-container log flood: " + str(len(misses)))
    ensure(inspect(OBS, "{{.State.Status}}") == "running", "event watcher died")
    facts["fast_remove_warning_count"] = len(misses)
    return "3 real --rm containers, " + str(len(misses)) + " bounded missing-ID warnings, observer alive"

def t12_healthcheck():
    vol = make_volume("healthcontrol")
    name = fixture("health", "import time;time.sleep(300)", control=vol,
        options=("--health-cmd",
                 "python3 -c 'import os,sys;sys.exit(0 if os.path.exists(\"/control/healthy\") else 1)'",
                 "--health-interval", "2s", "--health-timeout", "2s",
                 "--health-retries", "2", "--health-start-period", "0s"))
    start(name)
    wait_alert(name, "unhealthy", limit=55)
    ensure(must_count(name, "unhealthy", 1) == 1, "health alert duplicated")
    ensure('severity="HIGH"' in alerts(name, "unhealthy")[0], "unhealthy severity")
    write_control(vol, "healthy")
    wait_alert(name, "recovered", limit=40)
    ensure(must_count(name, "recovered", 1) == 1, "health recovery duplicated")
    ensure('severity="INFO"' in alerts(name, "recovered")[0], "recovery priority")
    ensure(inspect(name, "{{.State.Health.Status}}") == "healthy", "Docker health not healthy")
    return "real native Docker HEALTHCHECK unhealthy HIGH then healthy INFO recovery"

def t13_dfc():
    sent = [x for x in run(["logs", "--since", START, OBS]).splitlines()
            if 'event="watchdog_alert"' in x]
    received = require_syslog()
    (BASE/"dfc-syslog.txt").write_text("\n".join(received) + "\n")
    facts["watchdog_alert_count"] = len(sent)
    facts["dfc_received_count"] = len(received)
    ensure(sent, "watchdog generated no alert records")
    for name in (("crash", "sigkill", "oom", "loop", "reuse", "health", "external")
                 if ISOLATED else ("crash", "sigkill", "oom", "loop", "reuse", "health")):
        actual = PREFIX + "-" + name
        local = [x for x in sent if 'container="' + actual + '"' in x]
        remote = [x for x in received if 'container="' + actual + '"' in x]
        ensure(len(remote) == len(local),
               name + " alert count differs watchdog=" + str(len(local)) +
               " DFC=" + str(len(remote)))
    ensure(not [x for x in received if 'container="' + PREFIX + '-planned"' in x],
           "planned stop sent to DFC")
    ensure(not [x for x in received if 'container="' + PREFIX + '-clean"' in x],
           "clean exit sent to DFC")
    for kind,severity in (("crashed","CRITICAL"),("oom","CRITICAL"),
                          ("restart-loop","HIGH"),("unhealthy","HIGH"),
                          ("recovered","INFO")):
        ensure(any('failure_type="' + kind + '"' in x and
                   'severity="' + severity + '"' in x for x in received),
               "DFC missing " + kind + "/" + severity)
    return str(len(sent)) + " watchdog alerts delivered as " + str(len(received)) + \
           (" isolated UDP syslog messages" if ISOLATED else " DFC syslog messages") + \
           "; CRITICAL/HIGH/INFO fields correct"

def collect():
    stop_events()
    if OBS in containers:
        raw = run(["logs", "--since", START, OBS], timeout=30, check=False)
        (BASE/"watchdog.log").write_text(raw + "\n")
        try:
            vol = next(v for v in volumes if v.endswith("-state"))
            d = run(["run", "--rm", "--network", "none", "--user", "0:0",
                     "--volume", vol + ":/data", "--entrypoint", "python3",
                     IMAGE, "-c",
                     "import json;print(json.dumps(json.load(open('/data/state.json')),sort_keys=True))"],
                    timeout=30)
            (BASE/"observer-state.json").write_text(d + "\n")
        except Exception as e:
            (BASE/"state-read-error.txt").write_text(str(e))
    try:
        before = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
        ev = run(["events", "--since", START, "--until", before,
                  "--filter", "type=container",
                  "--filter", "label=" + LABEL], timeout=40, check=False)
        (BASE/"docker-events.log").write_text(ev + "\n")
        facts["docker_event_lines"] = len([x for x in ev.splitlines() if x.strip()])
    except Exception as e:
        facts["docker_events_error"] = str(e)
    try:
        if not (BASE/"dfc-syslog.txt").exists():
            (BASE/"dfc-syslog.txt").write_text("\n".join(require_syslog()) + "\n")
    except Exception as e:
        facts["syslog_read_error"] = str(e)
    for n in list(containers):
        if n == OBS:
            continue
        try:
            state_json = run(["inspect", "--format", "{{json .State}}", n], check=False)
            if state_json:
                facts.setdefault("final_fixture_states", {})[n] = json.loads(state_json)
        except Exception:
            pass

def cleanup():
    for n in reversed(containers):
        if n == OBS:
            continue
        try:
            run(["rm", "-f", n], timeout=30, check=False)
        except Exception as e:
            facts.setdefault("cleanup_errors", []).append(n + ": " + str(e))
    if OBS in containers:
        try:
            run(["stop", "-t", "12", OBS], timeout=25, check=False)
            run(["rm", "-f", OBS], timeout=25, check=False)
        except Exception as e:
            facts.setdefault("cleanup_errors", []).append(OBS + ": " + str(e))
    for vol in reversed(volumes):
        try:
            run(["volume", "rm", vol], timeout=30)
        except Exception as e:
            facts.setdefault("cleanup_errors", []).append(vol + ": " + str(e))

try:
    init()
    if os.environ.get("SMOKE_ONLY") == "T13_SYSLOG_QUICK":
        ensure(ISOLATED, "SMOKE_ONLY quick test requires isolated engine")
        checked("T02_UNEXPECTED_CRASH", "real CRITICAL crash", t02_crash)
        def quick_syslog():
            lines = require_syslog()
            ensure(any('container="' + PREFIX + '-crash"' in x and
                       'severity="CRITICAL"' in x for x in lines),
                   "no matching CRITICAL Syslog packet; raw=" +
                   (BASE/"syslog-udp-raw.txt").read_text()[:700])
            return "CRITICAL Syslog datagram collected for real Docker crash"
        checked("T13_SYSLOG_QUICK", "UDP receiver captures matching CRITICAL record", quick_syslog)
    elif os.environ.get("SMOKE_ONLY") in ("T14_EXTERNAL_RESTART", "T15_EXTERNAL_OOM"):
        ensure(ISOLATED, "SMOKE_ONLY external case must run on isolated engine")
        if os.environ["SMOKE_ONLY"] == "T14_EXTERNAL_RESTART":
            checked("T14_EXTERNAL_RESTART", "no recovery until 120 seconds after external start",
                    t14_external_restart)
        else:
            checked("T15_EXTERNAL_OOM", "real OOM then manual start requires 120s stability",
                    t15_external_oom_restart)
    else:
        checked("T01_CLEAN_EXIT", "exit=0 means no alert", t01_clean)
        checked("T02_UNEXPECTED_CRASH", "exit=2 sends one CRITICAL crash", t02_crash)
        checked("T03_PLANNED_STOP_137", "Docker stop exit 137 is suppressed", t03_planned_stop)
        checked("T04_DIRECT_SIGKILL", "SIGKILL 137 is crash, not OOM", t04_direct_kill)
        checked("T05_REAL_OOM", "OOMKilled=true sends only CRITICAL OOM", t05_oom)
        checked("T06_TO_T09_RESTART_LIFECYCLE",
                "CRASH -> RESTART-LOOP -> stability -> RECOVERED, interrupted recovery", t06_t08_loop)
        if ISOLATED:
            checked("T14_EXTERNAL_RESTART", "manual start after CRASH waits 120s before RECOVERED",
                    t14_external_restart)
        checked("T10_NAME_REUSE", "replacement with same name gets new incident", t10_name_reuse)
        checked("T11_FAST_REMOVAL", "no event flood, event listener survives", t11_fast_removal)
        checked("T12_NATIVE_HEALTHCHECK", "unhealthy -> recovered", t12_healthcheck)
        checked("T13_SYSLOG" if ISOLATED else "T13_DFC_SYSLOG",
                "all expected structured alerts reach " + ("test UDP collector" if ISOLATED else "DFC"),
                t13_dfc)
except Exception as e:
    issue("PREFLIGHT_OR_FATAL", "observer initializes and tests run", repr(e))
    facts["fatal_traceback"] = traceback.format_exc()
finally:
    try:
        collect()
    except Exception as e:
        facts["collect_error"] = repr(e)
    finally:
        stop_events()
    cleanup()
    finished = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
    facts["total_seconds"] = round(time.monotonic() - start_mono, 1)
    data = {"run_id": TAG, "started_utc": START, "finished_utc": finished,
            "status": ("FAIL" if any(x["status"] == "FAIL" for x in results)
                       or facts.get("cleanup_errors") else "PASS"),
            "image": IMAGE, "results": results, "facts": facts}
    (BASE/"report.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    lines = ["# Container Alert v1.6.0 real Docker smoke test",
             "", "**Run:** " + TAG + " | **Result:** " + data["status"],
             "**Started:** " + START + " | **Finished:** " + finished,
             "**Image:** " + IMAGE + " | **Revision:** " + str(facts.get("revision","unknown")),
             "**Host:** " + str(facts.get("host","unknown")) + " | **Observer host ID:** " + HOST,
             "", "## Acceptance results", "",
             "| Test | Status | Actual evidence |", "|---|---|---|"]
    for item in results:
        actual = str(item["observed"]).replace("|","/").replace("\n"," ")
        lines.append("| " + item["id"] + " | " + item["status"] + " | " + actual[:290] + " |")
    lines += ["", "## Evidence",
              "Actual Docker container failures and events; exact release code in an isolated observer.",
              "Observer has SMTP disabled; Syslog messages go to " + facts.get("syslog_backend", "receiver") + " UDP/514.",
              "No synthetic Docker events, mocked exporter metrics, host packages, or Pacemaker changes.",
              "Other watchdogs and unrelated existing containers are not modified.",
              "Output files: watchdog.log, docker-events.log, dfc-syslog.txt, observer-state.json, report.json.",
              "","## Facts",""]
    for k,v in sorted(facts.items()):
        if k == "final_fixture_states":
            continue
        lines.append("- **" + k + ":** " + str(v)[:400])
    lines += ["","**Cleanup:** only test-labelled disposable containers and volumes, " +
              ("PASS" if not facts.get("cleanup_errors") else "ERRORS"), ""]
    (BASE/"report.md").write_text("\n".join(lines))
    log("REPORT_DIR=" + str(BASE))
    log("SMOKE_RESULT=" + data["status"] + " checks=" + str(len(results)) +
        " fails=" + str(sum(x["status"] == "FAIL" for x in results)) +
        " runtime_seconds=" + str(facts["total_seconds"]))
    sys.exit(0 if data["status"] == "PASS" else 1)
PY
