"""Optional, read-only CyberController HA active-node monitoring.

Uses the existing Pacemaker HA exporter. No Pacemaker commands, privileges,
network ports, or third-party Prometheus parsing dependency are required.
"""
from dataclasses import dataclass
import json
import math
import re
from urllib.parse import urlsplit


MAX_METRICS_BYTES = 256 * 1024
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)="((?:\\.|[^"\\])*)"(?:,|$)')
_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z_0-9:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+\S+)?$')
_WANTED = {
    "ha_cluster_pacemaker_resources",
    "ha_cluster_corosync_member_votes",
    "ha_cluster_scrape_success",
    "ha_cluster_corosync_quorate",
}


@dataclass(frozen=True)
class HAObservation:
    active_node: str
    local_node: str


@dataclass(frozen=True)
class HAChange:
    previous: str
    current: str
    local_node: str

    @property
    def should_notify(self) -> bool:
        return self.local_node == self.current


def _labels(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    labels: dict[str, str] = {}
    offset = 0
    while offset < len(raw):
        match = _LABEL.match(raw, offset)
        if not match or match.group(1) in labels:
            raise ValueError("invalid or repeated Prometheus metric label")
        # JSON-style escaping matches the Prometheus text label syntax.
        value = json.loads(chr(34) + match.group(2) + chr(34))
        labels[match.group(1)] = value
        offset = match.end()
    return labels


def parse_ha_metrics(text: str) -> HAObservation:
    """Reject incomplete or ambiguous cluster state instead of guessing."""
    promoted: list[str] = []
    local: list[str] = []
    quorum: list[float] = []
    pacemaker_ok: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(None, 1)[0]
        if name not in _WANTED:
            continue
        match = _SAMPLE.match(line)
        if not match:
            raise ValueError("invalid HA metric sample")
        labels = _labels(match.group(2) or "")
        try:
            value = float(match.group(3))
        except ValueError as exc:
            raise ValueError("non-numeric HA metric") from exc
        if not math.isfinite(value):
            raise ValueError("non-finite HA metric")
        if name == "ha_cluster_scrape_success" and labels.get("collector") == "pacemaker":
            pacemaker_ok.append(value)
        elif name == "ha_cluster_corosync_quorate":
            quorum.append(value)
        elif name == "ha_cluster_corosync_member_votes" and labels.get("local") == "true" and value > 0:
            local.append(labels.get("node", ""))
        elif (name == "ha_cluster_pacemaker_resources"
              and labels.get("resource") == "HAOperatorRep-Res"
              and labels.get("role") == "promoted"
              and labels.get("status") == "active"
              and value == 1):
            promoted.append(labels.get("node", ""))

    if pacemaker_ok != [1] or quorum != [1]:
        raise ValueError("Pacemaker scrape failed or cluster has no quorum")
    if len(local) != 1 or not local[0] or len(local[0]) > 255:
        raise ValueError("cannot identify unique local HA node")
    if len(promoted) != 1 or not promoted[0] or len(promoted[0]) > 255:
        raise ValueError("cannot identify unique promoted HA Operator")
    return HAObservation(active_node=promoted[0], local_node=local[0])


def read_ha_observation(session, exporter_url: str, timeout_seconds: float = 3) -> HAObservation:
    """Read bounded, fresh local metrics without any new host permissions."""
    parts = urlsplit(exporter_url)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1")
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path != "/metrics" or not parts.port):
        raise ValueError("HA exporter URL must be local HTTP /metrics")
    response = session.get(exporter_url, timeout=timeout_seconds, stream=True, allow_redirects=False)
    try:
        if response.status_code != 200:
            raise ValueError(f"HA exporter returned HTTP {response.status_code}")
        raw = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            raw.extend(chunk)
            if len(raw) > MAX_METRICS_BYTES:
                raise ValueError("HA exporter response exceeded size limit")
        return parse_ha_metrics(raw.decode("utf-8"))
    finally:
        response.close()


class HARoleTracker:
    """Two-good-poll confirmation; persist only confirmed active owner."""
    def __init__(self, previous_active: str = ""):
        self.confirmed = previous_active
        self.candidate = ""
        self.candidate_count = 0

    def reset_candidate(self) -> None:
        self.candidate = ""
        self.candidate_count = 0

    def observe(self, obs: HAObservation) -> tuple[bool, HAChange | None]:
        """Return (confirmed_state_changed, optional role-change event)."""
        if not self.confirmed:
            self.confirmed = obs.active_node
            self.reset_candidate()
            return True, None  # Baseline, never alert on install/restart.
        if obs.active_node == self.confirmed:
            self.reset_candidate()
            return False, None
        if self.candidate != obs.active_node:
            self.candidate = obs.active_node
            self.candidate_count = 1
            return False, None
        self.candidate_count += 1
        if self.candidate_count < 2:
            return False, None
        previous = self.confirmed
        self.confirmed = obs.active_node
        self.reset_candidate()
        return True, HAChange(previous=previous, current=obs.active_node, local_node=obs.local_node)
