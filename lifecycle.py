"""Small, thread-safe Docker lifecycle event correlator.

Docker emits die and stop in either order for requested stops. A kill event by
itself is not evidence of an administrative stop (docker kill also emits kill).
The listener records events; the watchdog resolves pending deaths asynchronously.
"""

from dataclasses import dataclass
import threading
import time


@dataclass
class PendingDeath:
    container_id: str
    name: str
    exit_code: int | None
    container: object
    happened_at: float
    signals: tuple[str, ...] = ()
    stopped: bool = False
    oom: bool = False


class DockerLifecycleCorrelator:
    """Correlate stop/kill/oom with die, keyed by Docker container ID.

    The window is measured from event ingestion on a monotonic clock.
    Unknown or absent stop evidence must NOT suppress a crash.
    """

    def __init__(self, window_seconds: float = 3.0):
        self.window = max(0.0, float(window_seconds))
        self._lock = threading.Lock()
        self._pending: list[PendingDeath] = []
        self._recent_stops: dict[str, float] = {}
        self._recent_kills: dict[str, list[tuple[float, str]]] = {}
        self._signal_history_seconds = max(120.0, self.window)

    def record(
        self,
        action: str,
        container_id: str,
        *,
        name: str = "",
        container: object = None,
        exit_code: int | None = None,
        signal: str = "",
        now: float | None = None,
    ) -> bool:
        if not container_id:
            return False
        now = time.monotonic() if now is None else now
        matched = False
        with self._lock:
            # Prune bounded correlation evidence even for short-lived containers.
            self._recent_stops = {
                cid: at for cid, at in self._recent_stops.items()
                if 0 <= now - at <= self.window
            }
            self._recent_kills = {
                cid: [(at, sig) for at, sig in values
                      if 0 <= now - at <= self._signal_history_seconds]
                for cid, values in self._recent_kills.items()
                if any(0 <= now - at <= self._signal_history_seconds
                       for at, _ in values)
            }
            if action == "start":
                # Prevent a stop from a preceding run matching a new death.
                self._recent_stops.pop(container_id, None)
                self._recent_kills.pop(container_id, None)
            elif action == "kill":
                value = str(signal or "unknown")
                entries = self._recent_kills.setdefault(container_id, [])
                entries.append((now, value))
                if len(entries) > 8:
                    del entries[:-8]
                # Event delivery can be slightly out of order.
                for death in self._pending:
                    if death.container_id == container_id and 0 <= now - death.happened_at <= self.window:
                        death.signals = (*death.signals, value)[-8:]
            elif action == "stop":
                self._recent_stops[container_id] = now
                for death in self._pending:
                    if death.container_id == container_id and 0 <= now - death.happened_at <= self.window:
                        death.stopped = True
            elif action == "oom":
                for death in self._pending:
                    if death.container_id == container_id and 0 <= now - death.happened_at <= self.window:
                        death.oom = True
                        matched = True
            elif action == "die":
                stopped_at = self._recent_stops.get(container_id)
                stopped = stopped_at is not None and 0 <= now - stopped_at <= self.window
                signals = tuple(sig for _, sig in self._recent_kills.get(container_id, ()))
                self._pending.append(PendingDeath(
                    container_id=container_id, name=name, exit_code=exit_code,
                    container=container, happened_at=now,
                    signals=signals, stopped=stopped,
                ))
        return matched

    def drain_ready(self, *, now: float | None = None, force: bool = False) -> list[PendingDeath]:
        """Return resolved events. Unmatched deaths wait for the full window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            ready, waiting = [], []
            for death in self._pending:
                if force or death.stopped or death.oom or now - death.happened_at >= self.window:
                    ready.append(death)
                else:
                    waiting.append(death)
            self._pending = waiting
            return ready
