#!/usr/bin/env python3
"""
watchdog.py — CyberController Container Health Monitor
Monitors all Docker containers, detects failures, and dispatches
alerts via Slack, SMTP, SNMP, and Syslog.

Usage:
    python3 watchdog.py [--config /path/to/watchdog-config.yaml]
"""

import argparse
import logging
import logging.handlers
import os
import hashlib
import re
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



def setup_logging(level: str, log_file: str | None, syslog_cfg: dict | None = None) -> None:
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
    logging.basicConfig(level=numeric, format=LOG_FORMAT, handlers=handlers, force=True)
    # Suppress noisy third-party debug chatter (urllib3 Docker socket calls, etc.)
    for noisy in ("urllib3", "urllib3.connectionpool", "docker", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "alert_channels": ["slack"],
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

# Radware enterprise arc (PEN 89) for the container watchdog notification objects.
_ENTERPRISE_ARC = "1.3.6.1.4.1.89.110"
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


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



def dispatch_alert(payload: AlertPayload, cfg: dict) -> None:
    """Send alert to all configured channels."""
    channels = cfg.get("alert_channels", ["slack"])
    log.warning(
        "ALERT [%s] %s — %s (channels: %s)",
        payload.severity, payload.container_name,
        payload.failure_type, channels,
    )
    if "slack"  in channels: send_slack(payload, cfg)
    if "smtp"   in channels: send_smtp(payload, cfg)
    if "snmp_trap"  in channels: send_snmp_trap(payload, cfg)


def send_smtp(payload: AlertPayload, cfg: dict) -> None:
    """Send alert email via SMTP with production-grade error handling and retries."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formatdate

    smtp_cfg = cfg.get("smtp", {})
    if not smtp_cfg.get("enabled", False):
        log.debug("SMTP: disabled in config — skipping")
        return

    host         = smtp_cfg.get("host", "").strip()
    port         = int(smtp_cfg.get("port", 587))
    sender       = smtp_cfg.get("sender", "watchdog@localhost").strip()
    recipients   = smtp_cfg.get("recipients") or []
    use_tls      = smtp_cfg.get("tls", True)
    username_env = smtp_cfg.get("username_env", "SMTP_USERNAME")
    password_env = smtp_cfg.get("password_env", "SMTP_PASSWORD")
    username     = os.environ.get(username_env, "").strip()
    password     = os.environ.get(password_env, "").strip()
    # Authentication is ON by default (backwards compatible). Set `auth: false`
    # in the smtp config block for relays that don't require — or actively reject —
    # SMTP AUTH (common on internal / open relays).
    use_auth     = smtp_cfg.get("auth", True)

    # ── Validate configuration
    if not host:
        log.error("SMTP: host not configured")
        return
    if not sender:
        log.error("SMTP: sender address not configured")
        return

    # Normalize recipients to list of strings
    if isinstance(recipients, str):
        recipients = [recipients]
    elif not isinstance(recipients, (list, tuple)):
        recipients = list(recipients) if recipients else []
    recipients = [str(r).strip() for r in recipients if r]

    if not recipients:
        log.error("SMTP: no recipients configured")
        return

    if use_auth and (not username or not password):
        log.error(
            "SMTP: authentication is enabled but credentials are not set (%s, %s). "
            "Set them in .env, or set `auth: false` in the smtp config block if this "
            "relay does not require authentication.",
            username_env, password_env,
        )
        return

    # ── Build message
    subject = f"[{payload.severity}] {payload.container_name}: {payload.failure_type}"
    body = f"""Container: {payload.container_name}
Host: {payload.host}
Status: {payload.failure_type}
Severity: {payload.severity}
Time: {payload.timestamp}
Exit Code: {payload.exit_code if payload.exit_code is not None else 'N/A'}

{payload.recommended_action}"""
    
    # Build an RFC-compliant MIME message.  smtplib.sendmail(str) tries to
    # encode the whole message as ASCII, which breaks on Unicode text such as
    # the em dash in recommended_action.  quoted-printable UTF-8 keeps the SMTP
    # payload ASCII-safe while preserving readable Unicode for recipients.
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=False)
    msg.set_content(body, charset="utf-8", cte="quoted-printable")
    message = msg.as_bytes()

    # ── Send with exponential backoff retry
    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            log.debug(f"SMTP: attempt {attempt}/{max_retries} — connecting {host}:{port}")
            context = ssl.create_default_context()
            
            with smtplib.SMTP(host, port, timeout=20) as server:
                log.debug(f"SMTP: connected, TLS={use_tls}")
                if use_tls:
                    server.starttls(context=context)
                    log.debug("SMTP: TLS handshake completed")
                if use_auth:
                    log.debug(f"SMTP: logging in as {username_env}")
                    server.login(username, password)
                    log.debug(f"SMTP: authenticated, sending to {recipients}")
                else:
                    log.debug(f"SMTP: no-auth mode (auth disabled) — sending to {recipients}")
                server.sendmail(sender, recipients, message)
                log.debug("SMTP: sendmail completed")
            
            log.info(f"SMTP: alert sent to {len(recipients)} recipient(s)")
            return  # Success
            
        except smtplib.SMTPAuthenticationError as exc:
            log.error(f"SMTP: authentication failed — check credentials: {exc}")
            return  # Don't retry auth failures
            
        except smtplib.SMTPRecipientsRefused as exc:
            log.error(f"SMTP: one or more recipients refused: {exc}")
            return  # Don't retry recipient rejection
            
        except smtplib.SMTPSenderRefused as exc:
            log.error(f"SMTP: sender address rejected: {exc}")
            return  # Don't retry sender rejection
            
        except smtplib.SMTPDataError as exc:
            log.error(f"SMTP: message data rejected by server: {exc}")
            return  # Don't retry data errors
            
        except (smtplib.SMTPException, OSError, EOFError, BrokenPipeError) as exc:
            # Transient errors: retry
            exc_type = type(exc).__name__
            if attempt < max_retries:
                wait_secs = 2 ** attempt  # exponential: 2s, 4s
                log.warning(f"SMTP: {exc_type} (attempt {attempt}/{max_retries}), retrying in {wait_secs}s: {exc}")
                time.sleep(wait_secs)
            else:
                log.error(f"SMTP: {exc_type} after {max_retries} attempts: {exc}")
                return
                
        except Exception as exc:
            log.error(f"SMTP: {type(exc).__name__}: {exc}")
            return


def send_snmp_trap(payload: AlertPayload, cfg: dict) -> None:
    """Send an SNMP trap (v1, v2c, or v3) to a configured trap receiver.

    pysnmp 6.x / 7.x async API
    --------------------------
    This function uses pysnmp's asyncio-based high-level API (pysnmp >= 6.2)
    and wraps it with ``asyncio.run()`` for use in this synchronous watchdog.
    pysnmp 4.x is no longer supported — its synchronous generator API
    (``sendNotification`` as a generator) was removed in 5.x.

    SNMPv3 notes
    ------------
    * On first run, leave ``v3_local_engine_id`` unset. The function logs the
      auto-generated engine ID at INFO level. Copy that value into
      ``watchdog-config.yaml`` as ``v3_local_engine_id`` so it stays stable
      across restarts.
    * Register the same engine ID in snmptrapd.conf::

          createUser -e 0x<engine_id> <username> SHA "<auth_pw>" AES "<priv_pw>"
          authUser log,execute,net <username>

    Privacy protocol
    ----------------
    DES (``v3_priv_protocol: DES``) is still supported by pysnmp 7.x but is
    cryptographically weak (56-bit key, broken since ~2000). Prefer AES for
    any new deployment. The default is ``AES`` when nothing is configured.
    """
    import asyncio

    snmp = cfg.get("snmp_trap", {})
    if not snmp.get("enabled", False):
        log.debug("SNMP trap: disabled in config — skipping")
        return

    # ── 1. Import guard — pysnmp 6.x / 7.x required ──────────────────────────
    # pysnmp 7.x renamed constants to PEP-8 style (USM_AUTH_HMAC96_SHA, etc.).
    # Each group is imported separately so one missing name doesn't block all others.
    try:
        import pysnmp as _pysnmp_pkg
        _pysnmp_ver = tuple(int(x) for x in _pysnmp_pkg.__version__.split(".")[:2])
        if _pysnmp_ver < (5, 0):
            log.error(
                "SNMP trap: pysnmp %s is not supported — requires pysnmp 6.2 or later. "
                "pysnmp 4.x used a synchronous generator API that has since been "
                "removed. Fix: pip install 'pysnmp>=6.2'",
                _pysnmp_pkg.__version__,
            )
            return
        # Work around a circular-import bug in pysnmp 7.1.x: importing the AES
        # privacy module (rfc3826.priv.aes) can fail with "partially initialized
        # module ... has no attribute 'Aes'" because rfc3414.service ↔
        # eso.priv.aesbase ↔ rfc3826.priv.aes form an import cycle. The cycle is
        # only avoided when rfc3414.service is imported BEFORE anything else
        # touches the AES module — and importing pysnmp.hlapi.v3arch.asyncio can
        # trigger that broken load first, caching the half-initialised module.
        # So prime rfc3414.service HERE, before the hlapi import below.
        # Best-effort — ignore if the internal module layout differs.
        try:
            import pysnmp.proto.secmod.rfc3414.service  # noqa: F401
            import pysnmp.proto.secmod.rfc3826.priv.aes  # noqa: F401
        except Exception:
            pass
        # Core classes — stable across 6.x and 7.x
        from pysnmp.hlapi.v3arch.asyncio import (  # type: ignore[import]
            SnmpEngine, CommunityData, UsmUserData,
            UdpTransportTarget, ContextData, NotificationType, ObjectIdentity,
            OctetString,
        )
        # send_notification: PEP-8 name in 7.x, camelCase alias in 6.x
        try:
            from pysnmp.hlapi.v3arch.asyncio import send_notification as _send_notification  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import sendNotification as _send_notification  # type: ignore[import]
        # Auth no-auth constant
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_NONE as usmNoAuthProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmNoAuthProtocol  # type: ignore[import]
        # Auth: MD5
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC96_MD5 as usmHMACMD5AuthProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmHMACMD5AuthProtocol  # type: ignore[import]
        # Auth: SHA-1
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC96_SHA as usmHMACSHAAuthProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmHMACSHAAuthProtocol  # type: ignore[import]
        # Auth: SHA-224/256/384/512
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC128_SHA224 as usmHMACSHA224AuthProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmHMACSHA224AuthProtocol  # type: ignore[import]
            except ImportError:
                usmHMACSHA224AuthProtocol = usmHMACSHAAuthProtocol  # type: ignore[assignment]
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC192_SHA256 as usmHMACSHA256AuthProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmHMACSHA256AuthProtocol  # type: ignore[import]
            except ImportError:
                usmHMACSHA256AuthProtocol = usmHMACSHAAuthProtocol  # type: ignore[assignment]
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC256_SHA384 as usmHMACSHA384AuthProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmHMACSHA384AuthProtocol  # type: ignore[import]
            except ImportError:
                usmHMACSHA384AuthProtocol = usmHMACSHAAuthProtocol  # type: ignore[assignment]
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_AUTH_HMAC384_SHA512 as usmHMACSHA512AuthProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmHMACSHA512AuthProtocol  # type: ignore[import]
            except ImportError:
                usmHMACSHA512AuthProtocol = usmHMACSHAAuthProtocol  # type: ignore[assignment]
        # Priv: no-priv
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_PRIV_NONE as usmNoPrivProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmNoPrivProtocol  # type: ignore[import]
        # Priv: DES (weak but still supported)
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_PRIV_CBC56_DES as usmDESPrivProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmDESPrivProtocol  # type: ignore[import]
        # Priv: AES-128
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_PRIV_CFB128_AES as usmAES128PrivProtocol  # type: ignore[import]
        except ImportError:
            from pysnmp.hlapi.v3arch.asyncio import usmAES128PrivProtocol  # type: ignore[import]
        # Priv: AES-192 / AES-256
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_PRIV_CFB192_AES as usmAES192PrivProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmAES192PrivProtocol  # type: ignore[import]
            except ImportError:
                usmAES192PrivProtocol = usmAES128PrivProtocol  # type: ignore[assignment]
        try:
            from pysnmp.hlapi.v3arch.asyncio import USM_PRIV_CFB256_AES as usmAES256PrivProtocol  # type: ignore[import]
        except ImportError:
            try:
                from pysnmp.hlapi.v3arch.asyncio import usmAES256PrivProtocol  # type: ignore[import]
            except ImportError:
                usmAES256PrivProtocol = usmAES128PrivProtocol  # type: ignore[assignment]
    except ImportError:
        log.error("SNMP trap: pysnmp is not installed — pip install 'pysnmp>=6.2'")
        return

    # ── 2. Basic config ───────────────────────────────────────────────────────
    host     = snmp.get("host", "localhost")
    port     = int(snmp.get("port", 162))
    version  = snmp.get("version", "v2c").lower()  # v1, v2c, or v3
    trap_oid = snmp.get("trap_oid", "1.3.6.1.4.1.89.110.0.1")

    # ── 3. Determine the local engine ID ONCE ────────────────────────────────
    #   This single value is used for BOTH the message header AND for localising
    #   the v3 USM auth/priv keys. They MUST be identical, otherwise snmptrapd
    #   computes the HMAC with keys localised to a different engine ID and
    #   rejects the trap with "Verification failed".
    _local_eid_hex = snmp.get("v3_local_engine_id", "").strip()
    if _local_eid_hex[:2] in ("0x", "0X"):
        _local_eid_hex = _local_eid_hex[2:]
    if _local_eid_hex:
        if not _HEX_RE.match(_local_eid_hex):
            log.error(
                "SNMP trap: v3_local_engine_id contains non-hex characters: %r — skipping",
                _local_eid_hex,
            )
            return
        if len(_local_eid_hex) % 2 != 0:
            log.error(
                "SNMP trap: v3_local_engine_id has odd length (%d chars) — "
                "must be an even-length hex string — skipping",
                len(_local_eid_hex),
            )
            return
        _engine_id_bytes = bytes.fromhex(_local_eid_hex)
        _eid_source = "config"
    else:
        # Derive a stable engine ID from the hostname so it survives container
        # restarts without requiring v3_local_engine_id in the config.
        # Format: 0x80004fb8 05 <hostname_bytes_up_to_20> <4_byte_sha256_suffix>
        _hostname_bytes = socket.gethostname().encode()[:20]
        _stable_suffix  = hashlib.sha256(_hostname_bytes).digest()[:4]
        _engine_id_bytes = b'\x80\x00\x4f\xb8\x05' + _hostname_bytes + _stable_suffix
        _eid_source = "hostname-derived"

    _engine_id_octets = OctetString(_engine_id_bytes)

    # ── 4. Auth / priv protocol tables ───────────────────────────────────────
    # pysnmp 7.x supports all of these. DES is still present but cryptographically
    # weak (56-bit key) — accepted with a warning; AES is the recommended default.
    _AUTH = {
        "MD5":    usmHMACMD5AuthProtocol,
        "SHA":    usmHMACSHAAuthProtocol,
        "SHA224": usmHMACSHA224AuthProtocol,
        "SHA256": usmHMACSHA256AuthProtocol,
        "SHA384": usmHMACSHA384AuthProtocol,
        "SHA512": usmHMACSHA512AuthProtocol,
    }
    _PRIV = {
        "DES":    usmDESPrivProtocol,      # 56-bit — weak; accepted with warning
        "AES":    usmAES128PrivProtocol,   # AES-128 — recommended default
        "AES128": usmAES128PrivProtocol,
        "AES192": usmAES192PrivProtocol,
        "AES256": usmAES256PrivProtocol,
    }

    # ── 5. Security / community data ─────────────────────────────────────────
    if version == "v3":
        username       = snmp.get("v3_username", "")
        auth_proto_str = snmp.get("v3_auth_protocol", "SHA").upper()
        # Default is AES: DES is still functional but cryptographically weak.
        # New deployments should use AES; existing DES configs will log a warning.
        priv_proto_str = snmp.get("v3_priv_protocol", "AES").upper()
        # .strip() is essential: a stray newline (CRLF env files on Windows), a
        # trailing space, or a wrapping quote captured into these env vars changes
        # the localised USM key, so snmptrapd rejects the trap with
        # "usm: Verification failed" even though the passphrase "looks" identical.
        _auth_key_raw = os.environ.get(snmp.get("v3_auth_key_env", ""), "")
        _priv_key_raw = os.environ.get(snmp.get("v3_priv_key_env", ""), "")
        auth_key = _auth_key_raw.strip()
        priv_key = _priv_key_raw.strip()
        if auth_key != _auth_key_raw or priv_key != _priv_key_raw:
            log.warning(
                "SNMP trap: stripped surrounding whitespace/newlines from v3 key "
                "env vars (%s / %s) — clean that source (CRLF line endings or "
                "trailing spaces) to avoid 'Verification failed'.",
                snmp.get("v3_auth_key_env", ""), snmp.get("v3_priv_key_env", ""),
            )

        if not username:
            log.error("SNMP trap: v3_username is not set — skipping")
            return

        # DES is still supported by pysnmp 7.x (RFC 3414 §8) but is
        # cryptographically broken (56-bit key, exhausted by brute-force in hours).
        # Warn and continue — do not hard-error, as some NMS devices still only
        # support DES. New deployments should use AES.
        if priv_proto_str == "DES":
            log.warning(
                "SNMP trap: DES privacy protocol is cryptographically weak "
                "(56-bit key, broken since ~2000). Consider upgrading to AES: "
                "set v3_priv_protocol: AES and update snmptrapd.conf to match.",
            )

        # SNMPv3 has no privacy-only mode (RFC 3414): encryption requires
        # authentication. A priv key with no auth key would otherwise silently
        # fall through to noAuthNoPriv and send the trap UNENCRYPTED. Refuse.
        if priv_key and not auth_key:
            log.error(
                "SNMP trap: v3 priv key is set but no auth key was found — SNMPv3 "
                "cannot encrypt without authentication (no privacy-only mode). "
                "Set the auth key as well — skipping to avoid silently sending "
                "the trap unencrypted."
            )
            return

        # Reject unknown protocol names instead of silently downgrading — a silent
        # downgrade mismatches snmptrapd and surfaces as a baffling
        # "usm: Verification failed". Only validated when the key is in use.
        if auth_key and auth_proto_str not in _AUTH:
            log.error(
                "SNMP trap: unknown v3_auth_protocol %r — supported values: %s — skipping",
                auth_proto_str, ", ".join(sorted(_AUTH)),
            )
            return
        if priv_key and priv_proto_str not in _PRIV:
            log.error(
                "SNMP trap: unknown v3_priv_protocol %r — supported values: %s — skipping",
                priv_proto_str, ", ".join(sorted(_PRIV)),
            )
            return

        # SNMPv3 requires auth/priv passphrases of at least 8 characters (RFC 3414 §11.2).
        if auth_key and len(auth_key) < 8:
            log.error(
                "SNMP trap: v3 auth key is only %d characters — SNMPv3 requires at "
                "least 8 — skipping", len(auth_key),
            )
            return
        if priv_key and len(priv_key) < 8:
            log.error(
                "SNMP trap: v3 priv key is only %d characters — SNMPv3 requires at "
                "least 8 — skipping", len(priv_key),
            )
            return

        if auth_key and priv_key:
            security_data = UsmUserData(
                username,
                authKey=auth_key, authProtocol=_AUTH[auth_proto_str],
                privKey=priv_key, privProtocol=_PRIV[priv_proto_str],
                securityEngineId=_engine_id_octets,
            )
        elif auth_key:
            security_data = UsmUserData(
                username,
                authKey=auth_key, authProtocol=_AUTH[auth_proto_str],
                privProtocol=usmNoPrivProtocol,
                securityEngineId=_engine_id_octets,
            )
        else:
            log.warning(
                "SNMP trap: no auth key found for v3 user '%s' — "
                "sending noAuthNoPriv (not recommended for production)",
                username,
            )
            security_data = UsmUserData(
                username, authProtocol=usmNoAuthProtocol, privProtocol=usmNoPrivProtocol,
                securityEngineId=_engine_id_octets,
            )
    else:
        community     = snmp.get("community", "public")
        mp_model      = 0 if version == "v1" else 1  # 0=SNMPv1, 1=SNMPv2c
        security_data = CommunityData(community, mpModel=mp_model)

    summary = (
        f"[{payload.severity}] {payload.container_name} on {payload.host}: "
        f"{payload.failure_type}"
    )

    # ── 6. Log the effective engine ID (needed for snmptrapd createUser -e) ───
    if _eid_source == "config":
        log.debug(
            "SNMP engine ID (pinned from config): 0x%s",
            _engine_id_bytes.hex(),
        )
    else:
        log.info(
            "SNMP trap: v3_local_engine_id is not set — using auto-stable engine ID "
            "derived from hostname '%s'. This ID is consistent across restarts. "
            "Configure snmptrapd once with the engine ID logged below.",
            socket.gethostname(),
        )
    log.info(
        "SNMP engine ID: 0x%s "
        "(snmptrapd.conf: createUser -e 0x%s %s SHA \"...\" AES \"...\")",
        _engine_id_bytes.hex(), _engine_id_bytes.hex(),
        snmp.get("v3_username", "<username>"),
    )

    # ── 7. Send via asyncio (pysnmp 6.x/7.x uses coroutine-based API) ─────────
    # asyncio.run() creates a temporary event loop — safe because the watchdog
    # is entirely synchronous (threading-based, no running event loop).
    async def _do_send() -> tuple:
        # SnmpEngine MUST be created inside the running event loop (pysnmp 7.x
        # requirement — its internal asyncio dispatcher is bound to the loop).
        _engine = SnmpEngine(snmpEngineID=_engine_id_octets)

        # pysnmp 7.x has a known bug where TRAP sends (which need no response)
        # occasionally call future.set_result() twice, raising InvalidStateError
        # from asyncio's callback machinery. Suppress it — it is cosmetic and
        # does not affect trap delivery.
        def _suppress_invalid_state(loop, context):
            exc = context.get("exception")
            if isinstance(exc, asyncio.InvalidStateError):
                return
            loop.default_exception_handler(context)
        asyncio.get_event_loop().set_exception_handler(_suppress_invalid_state)

        try:
            # UdpTransportTarget.create() is an async factory in pysnmp 7.1+;
            # fall back to the synchronous constructor for 6.x / 7.0.
            _create = getattr(UdpTransportTarget, 'create', None)
            if _create is not None and asyncio.iscoroutinefunction(_create):
                transport = await UdpTransportTarget.create((host, port), timeout=5, retries=1)
            else:
                transport = UdpTransportTarget((host, port), timeout=5, retries=1)  # type: ignore[call-arg]

            # Build the notification. add_varbinds is the PEP-8 name (pysnmp 7.x);
            # addVarBinds is the legacy alias kept for 6.x compatibility.
            _notif = NotificationType(ObjectIdentity(trap_oid))
            _add_vb = getattr(_notif, 'add_varbinds', None) or getattr(_notif, 'addVarBinds')
            notification = _add_vb(
                # All var-binds under the Radware enterprise objects branch
                # 1.3.6.1.4.1.89.110.1.<n>.0 — RADWARE-CONTAINER-WATCHDOG-MIB.
                # cwHost (.1.1.0) — STRING — hostname of the alerting node
                (f"{_ENTERPRISE_ARC}.1.1.0", OctetString(payload.host.encode("utf-8", errors="replace"))),
                # cwSummary (.1.2.0) — STRING — human-readable alert summary
                (f"{_ENTERPRISE_ARC}.1.2.0", OctetString(summary.encode("utf-8", errors="replace"))),
                # cwContainerName (.1.3.0) — STRING — container name
                (f"{_ENTERPRISE_ARC}.1.3.0", OctetString(payload.container_name.encode("utf-8", errors="replace"))),
                # cwFailureType (.1.4.0) — STRING — crashed | oom | unhealthy | restart-loop
                (f"{_ENTERPRISE_ARC}.1.4.0", OctetString(payload.failure_type.encode("utf-8", errors="replace"))),
                # cwProbeDetail (.1.5.0) — STRING — what probe failed and why
                (f"{_ENTERPRISE_ARC}.1.5.0", OctetString((payload.probe_detail or "").encode("utf-8", errors="replace"))),
            )

            error_indication, error_status, error_index, _ = await _send_notification(
                _engine,
                security_data,    # CommunityData (v1/v2c) or UsmUserData (v3)
                transport,
                ContextData(),
                "trap",
                notification,
                lookupMib=False,  # skip MIB resolution — all OIDs are numeric
            )
            return error_indication, error_status, error_index
        finally:
            try:
                _engine.close_dispatcher()
            except Exception:
                pass

    try:
        error_indication, error_status, error_index = asyncio.run(_do_send())
        if error_indication:
            log.error(
                "SNMP trap failed to %s:%d: indication=%s, status=%s, index=%s",
                host, port, error_indication, error_status, error_index,
            )
        else:
            log.info("SNMP trap sent to %s:%d (version=%s)", host, port, version)
    except Exception as exc:
        import traceback as _tb
        tb_oneline = " | ".join(_tb.format_exc().splitlines())
        log.error(
            "SNMP trap error to %s:%d — %s: %r — traceback: %s",
            host, port, type(exc).__name__, exc, tb_oneline,
        )


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
        return f"Docker health probe failed - exit code {exit_code}, output: {output or '(no output)'}"
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
                                        probe_detail=f"Crash probe failed - container exited with exit code {exit_code}")
                dispatch_alert(payload, self.cfg)
                record_alert(state, failure_type)

        elif action == "oom":
            failure_type = "oom"
            self._oom_containers.add(name)  # mark so the imminent die event is suppressed
            if should_alert(state, failure_type, self._cooldown_minutes):
                payload = build_payload(container, failure_type, self.host,
                                        self.runbook_base,
                                        extra_context=get_memory_stats(container),
                                        probe_detail="OOM probe failed - container was OOM-killed by the kernel")
                dispatch_alert(payload, self.cfg)
                record_alert(state, failure_type)

        elif action == "health_status: unhealthy":
            state.unhealthy_cycles += 1
            threshold = self._unhealthy_threshold
            log.debug("%s unhealthy cycle %d/%d", name, state.unhealthy_cycles, threshold)
            if state.unhealthy_cycles >= threshold:
                failure_type = "unhealthy"
                if should_alert(state, failure_type, self._cooldown_minutes):
                    probe_detail = get_docker_health_log(container) or "Docker health probe failed - health_status: unhealthy event"
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
                                                probe_detail=f"Restart-loop probe failed - {len(state.restart_times)} restarts in {int(self._restart_window_secs // 60)}min window")
                        dispatch_alert(payload, self.cfg)
                        record_alert(state, failure_type)

            # Detect stuck containers missed by event stream
            elif status == "running" and health == "unhealthy":
                log.info("CONTAINER %-30s  status=%-12s  health=%s", name, status, health)
                state.unhealthy_cycles += 1
                if state.unhealthy_cycles >= unhealthy_threshold:
                    failure_type = "unhealthy"
                    if should_alert(state, failure_type, self._cooldown_minutes):
                        probe_detail = get_docker_health_log(container) or "Docker health probe failed - health_status: unhealthy (poll-detected)"
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
                detail = f"HTTP probe failed - error response {r.status_code} {r.reason or 'FAIL'}, URI {url}"
                log.debug("%s: auto-check %s \u2192 %d (%s)",
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
                            detail = f"HTTP probe failed - error response {r.status_code} {r.reason or 'FAIL'}, URI {url}"
                            return False, detail
                    except Exception:
                        continue
                # HTTP exhausted for this ip:port - try raw TCP connect
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
            detail = f"TCP probe failed - connection refused or timed out on {ip}:{port} ({exc})"
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
            detail = f"TCP probe failed - connection refused or timed out on {ip}:{port} ({exc})"
            log.debug("TCP connect %s:%d -> FAIL: %s", ip, port, exc)
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
            detail = f"/proc alive check failed - PID 1 is zombie/stopped (exit code {result.exit_code})"
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

