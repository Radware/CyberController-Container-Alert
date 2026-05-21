#!/usr/bin/env python3
"""
watchdog.py — CyberController Container Health Monitor
Monitors all Docker containers, detects failures, and dispatches
alerts via Slack and Splunk.

Usage:
    python3 watchdog.py [--config /path/to/watchdog-config.yaml]
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time
import threading
from datetime import datetime, timezone

import docker
import requests
import socket
import yaml

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
SYSLOG_FORMAT = "watchdog[%(process)d]: %(levelname)s %(name)s: %(message)s"
log = logging.getLogger("watchdog")


class SplunkHECHandler(logging.Handler):
    """Logging handler that ships records to Splunk via HTTP Event Collector."""

    def __init__(self, hec_url: str, token: str, index: str, verify_ssl: bool) -> None:
        super().__init__()
        self._url = hec_url.rstrip("/") + "/services/collector/event"
        self._headers = {
            "Authorization": f"Splunk {token}",
            "Content-Type": "application/json",
        }
        self._index = index
        self._verify_ssl = verify_ssl
        self._hostname = socket.gethostname()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = {
                "time": record.created,
                "host": self._hostname,
                "source": "watchdog",
                "sourcetype": "watchdog:log",
                "index": self._index,
                "event": {
                    "message": self.format(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "process": record.process,
                },
            }
            requests.post(
                self._url,
                json=event,
                headers=self._headers,
                timeout=5,
                verify=self._verify_ssl,
            )
        except Exception:
            self.handleError(record)


def setup_logging(level: str, log_file: str | None, syslog_cfg: dict | None = None, splunk_cfg: dict | None = None) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5
            )
        )
    if syslog_cfg and syslog_cfg.get("enabled"):
        host = syslog_cfg.get("host", "localhost")
        port = int(syslog_cfg.get("port", 514))
        proto = syslog_cfg.get("protocol", "udp").lower()
        socktype = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
        facility_name = syslog_cfg.get("facility", "local0").upper()
        facility = getattr(
            logging.handlers.SysLogHandler,
            f"LOG_{facility_name}",
            logging.handlers.SysLogHandler.LOG_LOCAL0,
        )
        syslog_handler = logging.handlers.SysLogHandler(
            address=(host, port),
            facility=facility,
            socktype=socktype,
        )
        syslog_handler.setFormatter(logging.Formatter(SYSLOG_FORMAT))
        handlers.append(syslog_handler)
        # Use a plain formatter for other handlers, syslog gets its own
        log.info("Syslog handler added: %s:%d (%s)", host, port, proto)
    if splunk_cfg and splunk_cfg.get("log_enabled"):
        hec_url = os.environ.get(splunk_cfg.get("hec_url_env", "SPLUNK_HEC_URL"), "")
        token   = os.environ.get(splunk_cfg.get("hec_token_env", "SPLUNK_HEC_TOKEN"), "")
        if hec_url and token:
            splunk_handler = SplunkHECHandler(
                hec_url=hec_url,
                token=token,
                index=splunk_cfg.get("log_index", "main"),
                verify_ssl=splunk_cfg.get("verify_ssl", True),
            )
            splunk_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            handlers.append(splunk_handler)
            log.info("Splunk HEC log handler added (index: %s)", splunk_cfg.get("log_index", "main"))
        else:
            log.warning("Splunk log handler: SPLUNK_HEC_URL or SPLUNK_HEC_TOKEN not set — skipping")
    logging.basicConfig(level=numeric, format=LOG_FORMAT, handlers=handlers, force=True)
    # Suppress noisy third-party debug chatter (urllib3 Docker socket calls, etc.)
    for noisy in ("urllib3", "urllib3.connectionpool", "docker", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "alert_channels": ["slack", "splunk"],
    "check_interval_seconds": 60,
    "cooldown_minutes": 5,
    "restart_threshold": 5,
    "restart_window_minutes": 10,
    "unhealthy_cycles_threshold": 3,
    "excluded_containers": [],
    "log_file": "/var/log/watchdog/watchdog.log",
    "log_level": "INFO",
    "syslog": {
        "enabled": False,
        "host": "localhost",
        "port": 514,
        "protocol": "udp",   # udp or tcp
        "facility": "local0",
    },
    "slack": {"webhook_url_env": "SLACK_WEBHOOK_URL"},
    "splunk": {
        "hec_url_env":   "SPLUNK_HEC_URL",
        "hec_token_env": "SPLUNK_HEC_TOKEN",
        "alert_index":   "main",
        "log_index":     "main",
        "log_enabled":   False,
        "verify_ssl":    True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg = _deep_merge(DEFAULT_CONFIG, user)
        log.info("Loaded config from %s", path)
    else:
        cfg = dict(DEFAULT_CONFIG)
        log.warning("Config file not found: %s — using defaults", path)
    return cfg


# ── Container state tracking ──────────────────────────────────────────────────
class ContainerState:
    __slots__ = ("name", "unhealthy_cycles", "restart_times", "last_alert_time", "alerted_for")

    def __init__(self, name: str):
        self.name = name
        self.unhealthy_cycles: int = 0
        self.restart_times: list[float] = []
        self.last_alert_time: float = 0.0
        self.alerted_for: str = ""


class WatchdogState:
    def __init__(self):
        self._lock = threading.Lock()
        self._states: dict[str, ContainerState] = {}

    def get(self, name: str) -> ContainerState:
        with self._lock:
            if name not in self._states:
                self._states[name] = ContainerState(name)
            return self._states[name]

    def cleanup_stale(self, active_names: set) -> None:
        with self._lock:
            stale = [n for n in list(self._states) if n not in active_names]
            for n in stale:
                self._states.pop(n, None)
        for n in stale:
            log.debug("Removing stale state for container: %s", n)


# ── Alert payload ─────────────────────────────────────────────────────────────
def _probe_type_label(detail: str) -> str:
    """Derive a short probe-type label from the probe_detail string."""
    if detail.startswith("HTTP probe failed"):
        return "HTTP GET"
    if detail.startswith("TCP probe failed"):
        return "TCP connect"
    if detail.startswith("Docker health probe failed"):
        return "Docker HEALTHCHECK"
    if detail.startswith("/proc alive check failed"):
        return "/proc alive check"
    if detail.startswith("Crash probe failed"):
        return "Docker event (crash)"
    if detail.startswith("OOM probe failed"):
        return "Docker event (OOM)"
    if detail.startswith("Restart-loop probe failed"):
        return "Restart-loop tracking"
    return ""


def _default_probe_type(failure_type: str) -> str:
    """Fallback probe-type label when probe_detail is absent."""
    return {
        "crashed":      "Docker event (crash)",
        "oom":          "Docker event (OOM)",
        "unhealthy":    "Docker HEALTHCHECK",
        "restart-loop": "Restart-loop tracking",
    }.get(failure_type, "")


class AlertPayload:
    def __init__(
        self,
        severity: str,
        container_name: str,
        container_id: str,
        host: str,
        failure_type: str,
        exit_code: int | None,
        log_tail: str,
        recommended_action: str,
        runbook_url: str,
        probe_detail: str = "",
    ):
        self.severity = severity           # CRITICAL / HIGH / WARNING
        self.container_name = container_name
        self.container_id = container_id[:12] if container_id else "N/A"
        self.host = host
        self.failure_type = failure_type  # crashed | oom | unhealthy | restart-loop
        self.exit_code = exit_code
        self.log_tail = log_tail
        self.recommended_action = recommended_action
        self.runbook_url = runbook_url
        self.probe_detail = probe_detail   # what probe failed and why
        self.probe_type  = _probe_type_label(probe_detail) or _default_probe_type(failure_type)
        self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def subject(self) -> str:
        probe = f" ({self.probe_type})" if self.probe_type else ""
        return (
            f"[{self.severity}] Container {self.failure_type.upper()}{probe}: "
            f"{self.container_name} on {self.host}"
        )


# ── Alert channels ────────────────────────────────────────────────────────────
_SLACK_COLORS = {"CRITICAL": "#C0392B", "HIGH": "#E67E22", "WARNING": "#F1C40F"}


def send_slack(payload: AlertPayload, cfg: dict) -> None:
    slack_cfg = cfg.get("slack", {})
    if not slack_cfg.get("enabled", True):  # default True for backwards-compat
        log.debug("Slack: disabled in config — skipping")
        return
    url_env = slack_cfg.get("webhook_url_env", "SLACK_WEBHOOK_URL")
    url = os.environ.get(url_env, "")
    if not url:
        log.warning("Slack: %s not set — skipping", url_env)
        return

    color = _SLACK_COLORS.get(payload.severity, "#7F8C8D")
    exit_field = (
        [{"title": "Exit Code", "value": str(payload.exit_code), "short": True}]
        if payload.exit_code is not None
        else []
    )
    probe_type_field = (
        [{"title": "Probe Type", "value": payload.probe_type, "short": True}]
        if payload.probe_type
        else []
    )
    probe_field = (
        [{"title": "Detection", "value": payload.probe_detail, "short": False}]
        if payload.probe_detail
        else []
    )
    body = {
        "attachments": [{
            "color": color,
            "title": payload.subject(),
            "fields": [
                {"title": "Container", "value": payload.container_name, "short": True},
                {"title": "Host",      "value": payload.host,           "short": True},
                {"title": "Failure",   "value": payload.failure_type,   "short": True},
                {"title": "Severity",  "value": payload.severity,       "short": True},
                {"title": "Time (UTC)","value": payload.timestamp,      "short": True},
                *exit_field,
                *probe_type_field,
                *probe_field,
                {"title": "Action",    "value": payload.recommended_action, "short": False},
                {"title": "Runbook",   "value": payload.runbook_url,    "short": False},
            ],
            "footer": f"Container ID: {payload.container_id}",
            "ts": int(time.time()),
        }]
    }
    try:
        r = requests.post(url, json=body, timeout=10)
        r.raise_for_status()
        log.info("Slack alert sent")
    except Exception as exc:
        log.error("Slack failed: %s", exc)


def send_splunk(payload: AlertPayload, cfg: dict) -> None:
    splunk = cfg.get("splunk", {})
    if not splunk.get("enabled", True):  # default True for backwards-compat
        log.debug("Splunk: disabled in config — skipping")
        return
    hec_url = os.environ.get(splunk.get("hec_url_env", "SPLUNK_HEC_URL"), "")
    token   = os.environ.get(splunk.get("hec_token_env", "SPLUNK_HEC_TOKEN"), "")
    if not hec_url or not token:
        log.warning("Splunk: SPLUNK_HEC_URL or SPLUNK_HEC_TOKEN not set — skipping")
        return

    verify_ssl = splunk.get("verify_ssl", True)
    index      = splunk.get("alert_index", "main")
    event_fields: dict = {
        "severity":           payload.severity,
        "container_name":     payload.container_name,
        "container_id":       payload.container_id,
        "host":               payload.host,
        "failure_type":       payload.failure_type,
        "timestamp":          payload.timestamp,
        "recommended_action": payload.recommended_action,
        "runbook_url":        payload.runbook_url,
        "log_tail":           payload.log_tail,
    }
    if payload.exit_code is not None:
        event_fields["exit_code"] = payload.exit_code
    if payload.probe_type:
        event_fields["probe_type"] = payload.probe_type
    if payload.probe_detail:
        event_fields["probe_detail"] = payload.probe_detail

    body = {
        "time":       int(time.time()),
        "host":       payload.host,
        "source":     "watchdog",
        "sourcetype": "container:alert",
        "index":      index,
        "event":      event_fields,
    }
    url = hec_url.rstrip("/") + "/services/collector/event"
    headers = {
        "Authorization": f"Splunk {token}",
        "Content-Type":  "application/json",
    }
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10, verify=verify_ssl)
        r.raise_for_status()
        log.info("Splunk alert sent (index: %s)", index)
    except Exception as exc:
        log.error("Splunk failed: %s", exc)


def dispatch_alert(payload: AlertPayload, cfg: dict) -> None:
    """Send alert to all configured channels."""
    channels = cfg.get("alert_channels", ["slack", "splunk"])
    log.warning(
        "ALERT [%s] %s — %s (channels: %s)",
        payload.severity, payload.container_name,
        payload.failure_type, channels,
    )
    if "slack"      in channels: send_slack(payload, cfg)
    if "splunk"     in channels: send_splunk(payload, cfg)
    if "smtp"       in channels: send_smtp(payload, cfg)
    if "snmp_trap"  in channels: send_snmp_trap(payload, cfg)


def send_smtp(payload: AlertPayload, cfg: dict) -> None:
    """Send alert email via SMTP."""
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg.get("enabled", False):
        log.debug("SMTP: disabled in config — skipping")
        return

    host         = smtp_cfg.get("host", "localhost")
    port         = int(smtp_cfg.get("port", 587))
    sender       = smtp_cfg.get("sender", "watchdog@localhost")
    recipients   = smtp_cfg.get("recipients") or []
    use_tls      = smtp_cfg.get("tls", True)
    password_env = smtp_cfg.get("password_env", "SMTP_PASSWORD")
    password     = os.environ.get(password_env, "")

    if not recipients:
        log.warning("SMTP: no recipients configured — skipping")
        return

    subject = f"[{payload.severity}] {payload.subject()}"
    lines = [
        f"Container : {payload.container_name}",
        f"Host      : {payload.host}",
        f"Failure   : {payload.failure_type}",
        f"Severity  : {payload.severity}",
        f"Time (UTC): {payload.timestamp}",
    ]
    if payload.exit_code is not None:
        lines.append(f"Exit Code : {payload.exit_code}")
    if payload.probe_type:
        lines.append(f"Probe Type: {payload.probe_type}")
    if payload.probe_detail:
        lines.append(f"Detection : {payload.probe_detail}")
    lines += ["", "Recommended Action:", f"  {payload.recommended_action}"]
    if payload.runbook_url:
        lines.append(f"\nRunbook: {payload.runbook_url}")
    if payload.log_tail:
        lines += ["", "--- Log Tail ---", payload.log_tail]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText("\n".join(lines), "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls(context=context)
            if password:
                server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        log.info("SMTP alert sent to %d recipient(s)", len(recipients))
    except Exception as exc:
        log.error("SMTP failed: %s", exc)


def send_snmp_trap(payload: AlertPayload, cfg: dict) -> None:
    """Send SNMPv2c trap to a configured trap receiver."""
    snmp = cfg.get("snmp_trap", {})
    if not snmp.get("enabled", False):
        log.debug("SNMP trap: disabled in config — skipping")
        return

    try:
        from pysnmp.hlapi import (  # type: ignore[import]
            sendNotification, SnmpEngine, CommunityData, UdpTransportTarget,
            ContextData, NotificationType, ObjectIdentity, OctetString,
        )
    except ImportError:
        log.error("SNMP trap: pysnmp is not installed — pip install pysnmp")
        return

    host      = snmp.get("host", "localhost")
    port      = int(snmp.get("port", 162))
    community = snmp.get("community", "public")
    # Configurable notification OID — replace with your enterprise OID if needed
    trap_oid  = snmp.get("trap_oid", "1.3.6.1.6.3.1.1.5.4")  # SNMPv2-MIB::linkUp placeholder

    summary = (
        f"[{payload.severity}] {payload.container_name} on {payload.host}: "
        f"{payload.failure_type}"
    )

    try:
        error_indication, error_status, error_index, _ = next(
            sendNotification(
                SnmpEngine(),
                CommunityData(community, mpModel=1),   # SNMPv2c
                UdpTransportTarget((host, port), timeout=5, retries=1),
                ContextData(),
                "trap",
                NotificationType(ObjectIdentity(trap_oid)).addVarBinds(
                    ("1.3.6.1.2.1.1.5.0", OctetString(payload.host)),
                    ("1.3.6.1.2.1.1.1.0", OctetString(summary)),
                    ("1.3.6.1.2.1.1.6.0", OctetString(payload.container_name)),
                    ("1.3.6.1.2.1.1.7.0", OctetString(payload.failure_type)),
                    ("1.3.6.1.2.1.1.8.0", OctetString(payload.probe_detail or "")),
                ),
            )
        )
        if error_indication:
            log.error("SNMP trap failed: %s", error_indication)
        else:
            log.info("SNMP trap sent to %s:%d", host, port)
    except Exception as exc:
        log.error("SNMP trap error: %s", exc)


# ── Failure type → remediation mapping ───────────────────────────────────────
ACTIONS = {
    "crashed": (
        "CRITICAL",
        "Check exit code and logs. Run: docker logs <name> --tail 50. "
        "If exit code 137, likely OOM — increase memory limit.",
    ),
    "oom": (
        "CRITICAL",
        "Container was OOM-killed. Increase memory limit in docker-compose.yaml "
        "or investigate memory leak. Run: docker stats <name>.",
    ),
    "unhealthy": (
        "HIGH",
        "Container is running but health probe is failing. "
        "Check the application inside the container. Run: docker inspect <name>.",
    ),
    "restart-loop": (
        "HIGH",
        "Container is in a restart loop. Check logs for crash reason. "
        "Consider increasing RestartSec or investigating root cause.",
    ),
}


def get_log_tail(container, lines: int = 20) -> str:
    try:
        return container.logs(tail=lines, timestamps=False).decode(errors="replace")
    except Exception as exc:
        return f"(failed to retrieve logs: {exc})"


def get_docker_health_log(container) -> str:
    """Return the output of the last Docker-native health check run, if any."""
    try:
        checks = (
            container.attrs.get("State", {})
            .get("Health", {})
            .get("Log", [])
        )
        if not checks:
            return ""
        last = checks[-1]
        output = (last.get("Output") or "").strip()
        exit_code = last.get("ExitCode", "?")
        return f"Docker health probe failed — exit code {exit_code}, output: {output or '(no output)'}"
    except Exception:
        return ""


def get_memory_stats(container) -> str:
    try:
        stats = container.stats(stream=False)
        mem   = stats.get("memory_stats", {})
        usage = mem.get("usage", 0)
        limit = mem.get("limit", 0)
        peak  = mem.get("max_usage", 0)
        def _mb(b: int) -> str:
            return f"{b / 1024 / 1024:.1f} MB"
        return (
            f"memory usage={_mb(usage)}  limit={_mb(limit)}  peak={_mb(peak)}"
        )
    except Exception as exc:
        return f"(memory stats unavailable: {exc})"


def get_container_id(container) -> str:
    try:
        return container.id or "N/A"
    except Exception:
        return "N/A"


def build_payload(
    container,
    failure_type: str,
    host: str,
    runbook_base: str,
    exit_code: int | None = None,
    extra_context: str = "",
    probe_detail: str = "",
) -> AlertPayload:
    severity, action = ACTIONS.get(failure_type, ("WARNING", "Investigate container."))
    log_tail = get_log_tail(container)
    if extra_context:
        log_tail = f"{extra_context}\n\n{log_tail}"
    return AlertPayload(
        severity=severity,
        container_name=container.name,
        container_id=get_container_id(container),
        host=host,
        failure_type=failure_type,
        exit_code=exit_code,
        log_tail=log_tail,
        recommended_action=action.replace("<name>", container.name),
        runbook_url=runbook_base,
        probe_detail=probe_detail,
    )


# ── Cooldown check ────────────────────────────────────────────────────────────
def should_alert(state: ContainerState, failure_type: str, cooldown_minutes: int) -> bool:
    now = time.time()
    cooldown_secs = cooldown_minutes * 60
    if (
        state.alerted_for == failure_type
        and (now - state.last_alert_time) < cooldown_secs
    ):
        remaining = cooldown_secs - (now - state.last_alert_time)
        log.debug(
            "%s: cooldown active for %s (%.0fs remaining)",
            state.name, failure_type, remaining,
        )
        return False
    return True


def record_alert(state: ContainerState, failure_type: str) -> None:
    state.last_alert_time = time.time()
    state.alerted_for = failure_type


# ── Core watchdog logic ───────────────────────────────────────────────────────
class Watchdog:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = WatchdogState()
        self.host = os.environ.get("WATCHDOG_HOST", socket.gethostname())
        self.runbook_base = cfg.get(
            "runbook_base_url", "https://wiki.radware.internal/runbooks"
        )
        self._excluded: set[str] = set(cfg.get("excluded_containers") or [])
        self._restart_threshold   = cfg.get("restart_threshold", 5)
        self._restart_window_secs = cfg.get("restart_window_minutes", 10) * 60
        self._unhealthy_threshold = cfg.get("unhealthy_cycles_threshold", 3)
        self._cooldown_minutes    = cfg.get("cooldown_minutes", 5)
        self._discovered_checks: dict[str, dict | None] = {}  # auto-discovery cache
        self._oom_containers: set[str] = set()  # suppress die alert when oom already fired
        self._auto_cfg: dict = cfg.get("auto_health_check", {})
        self._session = requests.Session()  # reuse connections for health probes
        self.client = docker.from_env()
        self._stop_event = threading.Event()

    # ── Event stream listener ─────────────────────────────────────────────────
    def _event_listener(self) -> None:
        log.info("Docker event listener started")
        filters = {"type": "container", "event": ["die", "oom", "health_status"]}
        try:
            for event in self.client.events(decode=True, filters=filters):
                if self._stop_event.is_set():
                    break
                self._handle_event(event)
        except Exception as exc:
            log.error("Event listener error: %s — restarting in 5s", exc)
            time.sleep(5)
            if not self._stop_event.is_set():
                threading.Thread(target=self._event_listener, daemon=True).start()

    def _handle_event(self, event: dict) -> None:
        action     = event.get("Action", "")
        attributes = event.get("Actor", {}).get("Attributes", {})
        name       = attributes.get("name", "unknown")

        if name in self._excluded:
            return

        log.debug("Docker event: %s on %s", action, name)

        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            log.warning("Container %s not found for event %s", name, action)
            return

        state = self.state.get(name)

        if action == "die":
            self._discovered_checks.pop(name, None)  # clear cache — container will restart
            # OOM kills also emit a die event immediately after — skip the crashed alert
            # to avoid duplicate: the oom alert already covers the incident
            if name in self._oom_containers:
                self._oom_containers.discard(name)
                log.info("%s: die event follows OOM — suppressing duplicate crashed alert", name)
                return
            # Containers already flagged as restart-loop: suppress the per-cycle crashed
            # alert — the restart-loop HIGH is the active signal for this incident.
            # A first-time crash on a container with a restart policy still fires crashed.
            if state.alerted_for == "restart-loop":
                log.info("%s: die event during active restart-loop — suppressing redundant crashed alert", name)
                return
            exit_code_str = attributes.get("exitCode", "0")
            try:
                exit_code = int(exit_code_str)
            except (ValueError, TypeError):
                exit_code = None
            # exit 0 = graceful stop, no alert
            if exit_code == 0:
                log.info("%s exited cleanly (code 0) — no alert", name)
                return
            failure_type = "crashed"
            if should_alert(state, failure_type, self._cooldown_minutes):
                payload = build_payload(container, failure_type, self.host,
                                        self.runbook_base, exit_code,
                                        probe_detail=f"Crash probe failed — container exited with exit code {exit_code}")
                dispatch_alert(payload, self.cfg)
                record_alert(state, failure_type)

        elif action == "oom":
            failure_type = "oom"
            self._oom_containers.add(name)  # mark so the imminent die event is suppressed
            if should_alert(state, failure_type, self._cooldown_minutes):
                payload = build_payload(container, failure_type, self.host,
                                        self.runbook_base,
                                        extra_context=get_memory_stats(container),
                                        probe_detail="OOM probe failed — container was OOM-killed by the kernel")
                dispatch_alert(payload, self.cfg)
                record_alert(state, failure_type)

        elif action == "health_status: unhealthy":
            state.unhealthy_cycles += 1
            threshold = self._unhealthy_threshold
            log.debug("%s unhealthy cycle %d/%d", name, state.unhealthy_cycles, threshold)
            if state.unhealthy_cycles >= threshold:
                failure_type = "unhealthy"
                if should_alert(state, failure_type, self._cooldown_minutes):
                    probe_detail = get_docker_health_log(container) or "Docker health probe failed — health_status: unhealthy event"
                    payload = build_payload(container, failure_type, self.host,
                                            self.runbook_base,
                                            probe_detail=probe_detail)
                    dispatch_alert(payload, self.cfg)
                    record_alert(state, failure_type)

        elif action == "health_status: healthy":
            if state.unhealthy_cycles > 0:
                log.info("%s recovered — resetting unhealthy counter", name)
            state.unhealthy_cycles = 0
            state.alerted_for = ""

    # ── Poll loop ─────────────────────────────────────────────────────────────
    def _poll_loop(self) -> None:
        interval = self.cfg.get("check_interval_seconds", 60)
        log.info("Poll loop started — interval: %ds", interval)
        while not self._stop_event.is_set():
            try:
                self._poll_all_containers()
            except Exception as exc:
                log.error("Poll error: %s", exc)
            self._stop_event.wait(interval)

    def _poll_all_containers(self) -> None:
        excluded            = self._excluded
        restart_threshold   = self._restart_threshold
        restart_window      = self._restart_window_secs
        unhealthy_threshold = self._unhealthy_threshold
        now                 = time.time()
        seen_names: set[str] = set()

        for container in self.client.containers.list(all=True):
            name = container.name
            if name in excluded:
                continue
            seen_names.add(name)

            try:
                container.reload()
            except Exception:
                continue

            status = container.status               # running, exited, restarting, etc.
            health = self._get_health(container)    # healthy, unhealthy, none
            state  = self.state.get(name)

            # Track restarts within rolling window
            state.restart_times = [t for t in state.restart_times
                                   if now - t < restart_window]

            # Detect restart loop via poll (supplement to event stream)
            if status == "restarting":
                log.info("CONTAINER %-30s  status=%-12s  health=%s", name, status, health)
                state.restart_times.append(now)
                if len(state.restart_times) >= restart_threshold:
                    failure_type = "restart-loop"
                    if should_alert(state, failure_type, self._cooldown_minutes):
                        payload = build_payload(container, failure_type,
                                                self.host, self.runbook_base,
                                                probe_detail=f"Restart-loop probe failed — {len(state.restart_times)} restarts in {int(self._restart_window_secs // 60)}min window")
                        dispatch_alert(payload, self.cfg)
                        record_alert(state, failure_type)

            # Detect stuck containers missed by event stream
            elif status == "running" and health == "unhealthy":
                log.info("CONTAINER %-30s  status=%-12s  health=%s", name, status, health)
                state.unhealthy_cycles += 1
                if state.unhealthy_cycles >= unhealthy_threshold:
                    failure_type = "unhealthy"
                    if should_alert(state, failure_type, self._cooldown_minutes):
                        probe_detail = get_docker_health_log(container) or "Docker health probe failed — health_status: unhealthy (poll-detected)"
                        payload = build_payload(container, failure_type,
                                                self.host, self.runbook_base,
                                                probe_detail=probe_detail)
                        dispatch_alert(payload, self.cfg)
                        record_alert(state, failure_type)

            elif status == "running" and health in ("healthy", "none"):
                # Determine which health check to run and get result first
                probe_detail = ""
                if health == "none" and self.cfg.get("auto_health_check", {}).get("enabled"):
                    is_healthy, probe_detail = self._auto_discover_health_check(container)
                else:
                    is_healthy = True  # Docker-native healthy, or no check configured

                # Log with watchdog-assessed health (replaces Docker's "none")
                display_health = health if health != "none" else ("healthy" if is_healthy else "unhealthy")
                log.info("CONTAINER %-30s  status=%-12s  health=%s", name, status, display_health)

                if not is_healthy:
                    state.unhealthy_cycles += 1
                    log.debug(
                        "%s: health check failed — cycle %d/%d",
                        name, state.unhealthy_cycles, unhealthy_threshold,
                    )
                    if state.unhealthy_cycles >= unhealthy_threshold:
                        failure_type = "unhealthy"
                        if should_alert(state, failure_type, self._cooldown_minutes):
                            payload = build_payload(container, failure_type,
                                                    self.host, self.runbook_base,
                                                    probe_detail=probe_detail)
                            dispatch_alert(payload, self.cfg)
                            record_alert(state, failure_type)
                else:
                    if state.unhealthy_cycles > 0:
                        log.info("%s: check recovered — resetting counters", name)
                    state.unhealthy_cycles = 0

            else:
                log.info("CONTAINER %-30s  status=%-12s  health=%s", name, status, health)

        self.state.cleanup_stale(seen_names)
        # Clean up discovery cache for containers no longer running
        for _name in self._discovered_checks.keys() - seen_names:
            self._discovered_checks.pop(_name, None)

    @staticmethod
    def _get_health(container) -> str:
        try:
            return (
                container.attrs.get("State", {})
                .get("Health", {})
                .get("Status", "none")
            )
        except Exception:
            return "none"

    @staticmethod
    def _get_container_ip(container) -> str:
        """Return the first available internal IP of a container."""
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                ip = net_info.get("IPAddress", "")
                if ip:
                    return ip
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_all_container_ips(container) -> list[str]:
        """Return all internal IPs of a container across every connected Docker network."""
        ips: list[str] = []
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                ip = net_info.get("IPAddress", "")
                if ip and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
        return ips

    @staticmethod
    def _get_container_ports(container) -> list[int]:
        """Return sorted list of unique internal TCP ports exposed by the container.

        Checks two sources:
        - Config.ExposedPorts  — set by EXPOSE in Dockerfile or --expose at build time
        - NetworkSettings.Ports — also captures ports added via `docker run --expose`
          at runtime (value is null when exposed but not published to the host)
        """
        ports: set[int] = set()
        try:
            # Source 1: image/config-level exposed ports
            exposed = container.attrs.get("Config", {}).get("ExposedPorts") or {}
            for port_proto in exposed:
                try:
                    ports.add(int(port_proto.split("/")[0]))
                except ValueError:
                    pass
        except Exception:
            pass
        try:
            # Source 2: runtime-level ports (catches `docker run --expose`)
            net_ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for port_proto in net_ports:
                if "/tcp" in port_proto:
                    try:
                        ports.add(int(port_proto.split("/")[0]))
                    except ValueError:
                        pass
        except Exception:
            pass
        return sorted(ports)

    def _auto_discover_health_check(self, container) -> tuple[bool, str]:
        """
        Auto-discover a working health endpoint for a container with health=none.
        Probe order per exposed port: HTTP paths → raw TCP connect.
        Falls back to /proc alive-check when no ports are exposed or all fail.
        Caches the result so re-discovery only happens once per container lifetime
        (cleared on restart).
        Returns (healthy, probe_detail).
        """
        name     = container.name
        paths    = self._auto_cfg.get("paths", ["/-/healthy", "/health", "/healthz", "/metrics", "/"])
        timeout  = int(self._auto_cfg.get("timeout_seconds", 3))
        discover_timeout = max(1, timeout // 2)  # faster timeout during discovery phase
        all_ips  = self._get_all_container_ips(container)
        if not all_ips:
            return True, ""  # can't check — don't false-alarm

        # Sentinel: "unset" = not yet probed, None = probed but no endpoint found
        cached = self._discovered_checks.get(name, "unset")

        if cached != "unset":
            if cached is None:
                return True, ""  # previously confirmed unreachable — skip
            if cached.get("type") == "proc":
                return self._proc_alive_check(container, timeout)
            # Use the IP that was proven reachable during discovery
            ip = cached.get("ip") or self._get_container_ip(container)
            if cached.get("type") == "tcp":
                return self._tcp_connect_check(ip, cached["port"], timeout)
            url = f"http://{ip}:{cached['port']}{cached['path']}"
            try:
                r = self._session.get(url, timeout=timeout)
                healthy = r.status_code < 500
                detail = f"HTTP probe failed — error response {r.status_code} {r.reason or 'FAIL'}, URI {url}"
                log.debug("%s: auto-check %s → %d (%s)",
                          name, url, r.status_code, "ok" if healthy else "FAIL")
                return healthy, ("" if healthy else detail)
            except Exception as exc:
                log.debug("%s: auto-check error: %s — clearing cache for re-discovery", name, exc)
                self._discovered_checks.pop(name, None)
                return True, ""  # transient error — don't false-alarm

        # Discovery phase — probe all IPs × exposed ports × common paths
        ports = self._get_container_ports(container)
        if not ports:
            log.debug("%s: auto-discovery — no exposed ports, falling back to /proc alive-check", name)
            healthy, detail = self._proc_alive_check(container, timeout)
            self._discovered_checks[name] = {"type": "proc"}
            return healthy, detail

        log.debug("%s: auto-discovery probing IPs %s ports %s", name, all_ips, ports)
        last_tcp_failure: tuple[str, int, OSError] | None = None  # (ip, port, exc)
        for ip in all_ips:
            for port in ports:
                for path in paths:
                    url = f"http://{ip}:{port}{path}"
                    try:
                        r = self._session.get(url, timeout=discover_timeout)
                        if r.status_code < 500:
                            log.info("%s: auto-discovered HTTP endpoint: %s (HTTP %d)",
                                     name, url, r.status_code)
                            self._discovered_checks[name] = {"ip": ip, "port": port, "path": path}
                            return True, ""
                        else:
                            # HTTP error response — this IS the service endpoint; cache and report
                            log.info("%s: auto-discovered HTTP endpoint (failing): %s (HTTP %d)",
                                     name, url, r.status_code)
                            self._discovered_checks[name] = {"ip": ip, "port": port, "path": path}
                            detail = f"HTTP probe failed — error response {r.status_code} {r.reason or 'FAIL'}, URI {url}"
                            return False, detail
                    except Exception:
                        continue
                # HTTP exhausted for this ip:port — try raw TCP connect
                try:
                    with socket.create_connection((ip, port), timeout=discover_timeout):
                        pass
                    log.info("%s: auto-discovered TCP endpoint: %s:%d", name, ip, port)
                    self._discovered_checks[name] = {"type": "tcp", "ip": ip, "port": port}
                    return True, ""
                except OSError as exc:
                    # TCP refused on this port — record it but keep trying remaining ports
                    log.debug("%s: TCP connect %s:%d failed during discovery (%s) — trying next port", name, ip, port, exc)
                    last_tcp_failure = (ip, port, exc)
                    continue

        # All ports exhausted with only TCP failures — no HTTP service found on any port.
        # If every port refused TCP, cache the last one as the probe endpoint and report unhealthy.
        if last_tcp_failure is not None:
            ip, port, exc = last_tcp_failure
            log.info("%s: auto-discovered TCP endpoint (failing, all ports tried): %s:%d — %s", name, ip, port, exc)
            self._discovered_checks[name] = {"type": "tcp", "ip": ip, "port": port}
            detail = f"TCP probe failed — connection refused or timed out on {ip}:{port} ({exc})"
            return False, detail

        # No HTTP or TCP endpoint found — fall back to /proc alive-check
        log.debug("%s: auto-discovery — no reachable endpoint on any port, falling back to /proc alive-check", name)
        healthy, detail = self._proc_alive_check(container, timeout)
        self._discovered_checks[name] = {"type": "proc"}
        return healthy, detail


    def _tcp_connect_check(self, ip: str, port: int, timeout: int) -> tuple[bool, str]:
        """
        TCP connect probe — verifies the port accepts connections.
        Used for non-HTTP services (databases, message brokers, custom protocols).
        Returns (healthy, probe_detail).
        """
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                pass
            log.debug("TCP connect %s:%d → OK", ip, port)
            return True, ""
        except OSError as exc:
            detail = f"TCP probe failed — connection refused or timed out on {ip}:{port} ({exc})"
            log.debug("TCP connect %s:%d → FAIL: %s", ip, port, exc)
            return False, detail

    def _proc_alive_check(self, container, timeout: int) -> tuple[bool, str]:
        """
        Fallback check for containers with no HTTP endpoint.
        Reads /proc/1/status inside the container and verifies PID 1 is in a
        live state (R=running, S=sleeping, D=disk-wait).
        A zombie (Z) or stopped (T) PID 1 means the container is effectively dead.
        Works on all Linux images — requires only /proc (always present).
        Returns (healthy, probe_detail).
        """
        cmd = ["sh", "-c",
               "grep -qE 'State:[[:space:]]+(R|S|D)' /proc/1/status 2>/dev/null"]
        try:
            result = container.exec_run(cmd, demux=False)
            if result.exit_code == 126:
                # sh not available (distroless) — can't check, don't false-alarm
                log.debug("%s: /proc alive-check — sh not available, skipping", container.name)
                return True, ""
            healthy = result.exit_code == 0
            detail = f"/proc alive check failed — PID 1 is zombie/stopped (exit code {result.exit_code})"
            log.debug(
                "%s: /proc alive-check → exit %d (%s)",
                container.name, result.exit_code, "ok" if healthy else "FAIL (zombie/stopped)",
            )
            return healthy, ("" if healthy else detail)
        except Exception as exc:
            log.debug("%s: /proc alive-check error: %s", container.name, exc)
            return True, ""  # don't false-alarm on errors

    # ── Start / Stop ──────────────────────────────────────────────────────────
    def start(self) -> None:
        log.info("Watchdog starting — host: %s", self.host)
        event_thread = threading.Thread(
            target=self._event_listener, name="event-listener", daemon=True
        )
        event_thread.start()
        # Poll runs in the main thread
        try:
            self._poll_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        log.info("Watchdog shutting down")
        self._stop_event.set()


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="CyberController Container Watchdog")
    parser.add_argument(
        "--config",
        default=os.environ.get("WATCHDOG_CONFIG", "/etc/watchdog/watchdog-config.yaml"),
        help="Path to watchdog-config.yaml",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(
        cfg.get("log_level", "INFO"),
        cfg.get("log_file"),
        cfg.get("syslog"),
        cfg.get("splunk"),
    )

    watchdog = Watchdog(cfg)

    def _handle_signal(sig, _frame):
        log.info("Received signal %s", sig)
        watchdog.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    watchdog.start()


if __name__ == "__main__":
    main()
