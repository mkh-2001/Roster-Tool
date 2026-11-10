"""When should the desktop app exit? When no Roster Tool browser tab is open.

The app is one process (server + this monitor, no child processes), so
exiting can never leave an orphaned server behind. The UI tells the monitor
about itself:

* every open tab sends a heartbeat (every 30 s; browsers may slow hidden tabs
  to about once a minute, which the generous timeout allows for);
* a closing tab sends "goodbye" -> the app exits GOODBYE_GRACE seconds after
  the last tab leaves, unless a tab comes back (e.g. a page reload);
* the Quit button asks the app to exit immediately.

Safety nets: a tab that stops sending heartbeats (browser crashed, tab
discarded) is forgotten after IDLE_TIMEOUT, and if no tab ever connects (the
browser never opened) the app exits after STARTUP_TIMEOUT. A computer that
was asleep is not mistaken for a silent tab.

Timeouts can be shortened for automated tests with ROSTER_TOOL_STARTUP_TIMEOUT,
ROSTER_TOOL_IDLE_TIMEOUT and ROSTER_TOOL_GOODBYE_GRACE (seconds).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

STARTUP_TIMEOUT = 180.0
IDLE_TIMEOUT = 300.0
GOODBYE_GRACE = 10.0
TICK = 1.0
MAX_CLIENTS = 50

log = logging.getLogger("roster_tool.lifecycle")


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
        return value if value > 0 else default
    except ValueError:
        return default


class SessionMonitor:
    def __init__(
        self,
        on_exit: Callable[[str], None],
        startup_timeout: float | None = None,
        idle_timeout: float | None = None,
        goodbye_grace: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.on_exit = on_exit
        self.startup_timeout = startup_timeout or _env_seconds("ROSTER_TOOL_STARTUP_TIMEOUT", STARTUP_TIMEOUT)
        self.idle_timeout = idle_timeout or _env_seconds("ROSTER_TOOL_IDLE_TIMEOUT", IDLE_TIMEOUT)
        self.goodbye_grace = goodbye_grace or _env_seconds("ROSTER_TOOL_GOODBYE_GRACE", GOODBYE_GRACE)
        self.clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, float] = {}
        self._ever_connected = False
        self._started = clock()
        self._empty_since: float | None = None
        self._exit_reason: str | None = None
        self._stopped = threading.Event()

    # -- called by the server (any thread) ---------------------------------

    def seen(self, client: str) -> None:
        with self._lock:
            if client not in self._clients:
                if len(self._clients) >= MAX_CLIENTS:
                    return
                log.info("browser tab connected (%d open)", len(self._clients) + 1)
            self._clients[client] = self.clock()
            self._ever_connected = True
            self._empty_since = None

    def goodbye(self, client: str) -> None:
        with self._lock:
            if self._clients.pop(client, None) is not None:
                log.info("browser tab closed (%d open)", len(self._clients))
            if not self._clients and self._ever_connected:
                self._empty_since = self.clock()

    def request_exit(self, reason: str) -> None:
        with self._lock:
            self._exit_reason = self._exit_reason or reason

    # -- decision -------------------------------------------------------------

    def check(self) -> str | None:
        """The reason to exit now, or None to keep running."""
        now = self.clock()
        with self._lock:
            if self._exit_reason:
                return self._exit_reason
            for client, last in list(self._clients.items()):
                if now - last > self.idle_timeout:
                    del self._clients[client]
                    log.info("browser tab stopped responding (%d open)", len(self._clients))
                    if not self._clients:
                        self._empty_since = now
            if self._clients:
                return None
            if not self._ever_connected:
                return "no browser tab connected" if now - self._started > self.startup_timeout else None
            if self._empty_since is not None and now - self._empty_since >= self.goodbye_grace:
                return "all Roster Tool tabs closed"
            return None

    def resumed(self) -> None:
        """After the computer slept: give open tabs time to check in again."""
        now = self.clock()
        with self._lock:
            for client in self._clients:
                self._clients[client] = now
            self._started = now if not self._ever_connected else self._started

    # -- loop ---------------------------------------------------------------------

    def run(self) -> None:
        """Blocks until it is time to exit, then calls on_exit(reason)."""
        last_tick = self.clock()
        while not self._stopped.wait(TICK):
            now = self.clock()
            if now - last_tick > 30 * TICK:  # the process was suspended (sleep)
                log.info("resumed after the computer slept")
                self.resumed()
            last_tick = now
            reason = self.check()
            if reason:
                self.on_exit(reason)
                return

    def stop(self) -> None:
        self._stopped.set()
