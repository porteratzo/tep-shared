"""Reconnect rate limiting shared by modules that retry a flaky link (PLCs, cameras).

Promoted from ``plc_integration.base_plc``; ``plc_integration`` re-exports it.
"""

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ReconnectBackoff:
    """Rate-limits reconnection attempts with exponential backoff.

    A connect attempt against an unreachable PLC blocks for up to the driver
    timeout, and ``read_tag`` is called inline on the inspection path, so a
    reconnect is attempted at most once per ``delay`` seconds. The delay starts
    at ``initial``, doubles after each failed attempt up to ``maximum``, and
    resets on success. ``initial <= 0`` disables automatic reconnection.
    """

    initial: float = 2.0
    maximum: float = 60.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _delay: float = field(init=False, repr=False)
    _next_attempt: float = field(init=False, default=0.0, repr=False)

    def __post_init__(self) -> None:
        self._delay = self.initial

    @property
    def enabled(self) -> bool:
        return self.initial > 0

    def ready(self) -> bool:
        """True if enough time has passed since the last failed attempt."""
        return self.enabled and self.clock() >= self._next_attempt

    def record_failure(self) -> None:
        self._next_attempt = self.clock() + self._delay
        self._delay = min(self._delay * 2, max(self.maximum, self.initial))

    def reset(self) -> None:
        self._delay = self.initial
        self._next_attempt = 0.0
