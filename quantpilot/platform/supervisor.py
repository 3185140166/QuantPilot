"""Bounded restarts for only the processes owned by the unified launcher."""

from collections import deque
from dataclasses import dataclass, field


@dataclass
class ManagedService:
    name: str
    spawn: object
    process: object | None = None
    attempts: deque = field(default_factory=deque)
    restart_count: int = 0
    restart_at: float | None = None
    exit_code: int | None = None
    state: str = "starting"

    def launch(self):
        try:
            self.process = self.spawn()
            self.state = "running"
        except OSError:
            self.process, self.exit_code = None, -1
            self.state = "starting"

    def step(self, now, limit=5, window=600):
        if self.state == "failed":
            return False
        while self.attempts and self.attempts[0] < now - window:
            self.attempts.popleft()
        if self.process is None and self.restart_at is None:
            self.launch()
        code = self.process.poll() if self.process is not None else -1
        if code is None:
            return True
        self.exit_code = code
        if self.restart_at is None:
            if len(self.attempts) >= limit:
                self.state = "failed"
                return False
            self.restart_at = now + min(30, 2 ** len(self.attempts))
            self.state = "waiting"
        if now >= self.restart_at:
            self.attempts.append(now)
            self.restart_count += 1
            self.restart_at = None
            self.launch()
        return True

    def status(self):
        return {
            "name": self.name,
            "state": self.state,
            "restart_count": self.restart_count,
            "last_exit_code": self.exit_code,
        }
