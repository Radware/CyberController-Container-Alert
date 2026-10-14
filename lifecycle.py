"""Short-lived Docker stop/die evidence, not another alert-state machine.

Docker can deliver the final stop event just after die. Buffer only unexpected
nonzero exits for a bounded time; when no stop evidence arrives, report the
failure through Watchdog's existing incident engine.
"""
from dataclasses import dataclass
import threading
import time


@dataclass
class PendingDeath:
    container_id: str
    name: str
    container: object
    exit_code: int
    event_time: float | None
    happened_at: float
    stopped: bool = False
    oom: bool = False
    new_run_started: bool = False


class DockerLifecycleCorrelator:
    def __init__(self, window_seconds: float = 3.0):
        self.window = float(window_seconds)
        self._lock = threading.Lock()
        self._pending: list[PendingDeath] = []
        self._recent_stops: dict[str, float] = {}

    def record(self, action: str, container_id: str, *, name: str = "",
               container: object = None, exit_code: int = 1,
               event_time: float | None = None, now: float | None = None) -> bool:
        """Return whether an OOM event matched an already pending death."""
        if not container_id:
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            self._recent_stops = {
                cid: when for cid, when in self._recent_stops.items()
                if 0 <= now - when <= self.window
            }
            if action == "start":
                self._recent_stops.pop(container_id, None)
                # A later run's stop cannot excuse a prior run's failure.
                for death in self._pending:
                    if death.container_id == container_id:
                        death.new_run_started = True
            elif action == "stop":
                self._recent_stops[container_id] = now
                for death in reversed(self._pending):
                    if (death.container_id == container_id and not death.new_run_started
                            and 0 <= now - death.happened_at <= self.window):
                        death.stopped = True
                        break  # One stop must not mask several restart-cycle deaths.
            elif action == "oom":
                matched = False
                for death in reversed(self._pending):
                    if (death.container_id == container_id and not death.new_run_started
                            and 0 <= now - death.happened_at <= self.window):
                        death.oom = True
                        matched = True
                        break
                return matched
            elif action == "die":
                stopped_at = self._recent_stops.get(container_id)
                stopped = stopped_at is not None and 0 <= now - stopped_at <= self.window
                self._pending.append(PendingDeath(
                    container_id, name, container, exit_code, event_time, now,
                    stopped=stopped,
                ))
        return False

    def drain_ready(self, *, now: float | None = None, force: bool = False) -> list[PendingDeath]:
        now = time.monotonic() if now is None else now
        with self._lock:
            ready, waiting = [], []
            for death in self._pending:
                if (force or death.stopped or death.oom or death.new_run_started
                        or now - death.happened_at >= self.window):
                    ready.append(death)
                else:
                    waiting.append(death)
            self._pending = waiting
            return ready
