"""Unified proactive floodgate: per-account, per-operation sliding
windows + FloodWait circuit breaking for EVERY Telegram touchpoint
(send / history backfill / entity resolve / media download).

Design borrowed from production Telegram-crawler floodgates: instead
of pacing only outbound messages, every operation category gets its
own independent bucket so a deep history walk or a burst of
resolves can never trip Telegram's tiered long bans while sends keep
flowing (and vice versa). An account-level cooldown circuit breaker
on top turns any official FloodWait into a clean pause everywhere at
once — no retry storms, no ban escalation.

Every bucket keeps a per-account timestamp queue and accepts an
injected clock, so unit tests are fully deterministic. Env knobs:

    NEXUS_BRIDGE_SEND_MIN_GAP_S      (3.0)   NEXUS_BRIDGE_SEND_PER_MINUTE      (20)
    NEXUS_BRIDGE_HISTORY_MIN_GAP_S   (1.5)   NEXUS_BRIDGE_HISTORY_PER_MINUTE   (30)
    NEXUS_BRIDGE_RESOLVE_MIN_GAP_S   (2.0)   NEXUS_BRIDGE_RESOLVE_PER_MINUTE   (12)
    NEXUS_BRIDGE_MEDIA_MIN_GAP_S     (0.25)  NEXUS_BRIDGE_MEDIA_PER_MINUTE     (90)
"""
import os
import time

# ---------------------------------------------------------------------------
# FloodWait detection (telethon raises FloodWaitError / FloodPremiumWaitError
# with .seconds; pyrogram FloodWait carries .value — same convention as
# runner.duty_collection)
# ---------------------------------------------------------------------------

_FLOOD_WAIT_NAMES = {"FloodWaitError", "FloodPremiumWaitError", "FloodWait"}


def is_flood_wait(exc) -> bool:
    return type(exc).__name__ in _FLOOD_WAIT_NAMES


def flood_wait_seconds(exc) -> int:
    for attr in ("seconds", "value"):
        raw = getattr(exc, attr, None)
        try:
            if raw:
                return max(1, int(raw))
        except (TypeError, ValueError):
            continue
    return 1


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class SlidingWindowGate:
    """One operation category, many accounts: minimum gap between
    consecutive calls plus a per-minute cap, both sliding-window on an
    injectable monotonic clock."""

    def __init__(self, category: str, min_gap_s: float, per_minute: int, clock=None):
        self.category = category
        self._min_gap = float(min_gap_s)
        self._per_minute = int(per_minute)
        self._clock = clock or time.monotonic
        self._last: dict = {}
        self._window: dict = {}

    def delay_for(self, account_id) -> float:
        """Seconds this account must still wait before the next call."""
        now = self._clock()
        stamps = [
            stamp for stamp in self._window.get(account_id, [])
            if now - stamp < 60.0
        ]
        # sliding-window eviction: stamps older than a minute can never
        # count again, so dropping them keeps the queue small and exact
        self._window[account_id] = stamps
        delay = 0.0
        if self._per_minute > 0 and len(stamps) >= self._per_minute:
            delay = max(delay, 60.0 - (now - stamps[0]))
        last = self._last.get(account_id)
        if last is not None and self._min_gap > 0:
            delay = max(delay, self._min_gap - (now - last))
        return max(0.0, delay)

    def record(self, account_id) -> None:
        """Mark a call as happened right now."""
        now = self._clock()
        self._last[account_id] = now
        self._window.setdefault(account_id, []).append(now)


class FloodWaitCooldown:
    """Account-level circuit breaker for official FloodWait responses.

    While an account is tripped, every category of the gate refuses to
    let it through (delay_for takes the max), turning a FloodWait into
    a clean pause everywhere instead of a retry storm."""

    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._until: dict = {}

    def trip(self, account_id, seconds) -> float:
        """Trip the breaker; returns the effective cooldown seconds."""
        return self.cool(account_id, seconds)

    def cool(self, account_id, seconds) -> float:
        effective = max(1, int(seconds))
        deadline = self._clock() + effective
        self._until[account_id] = max(self._until.get(account_id, 0), deadline)
        return effective

    def remaining(self, account_id) -> float:
        return max(0.0, self._until.get(account_id, 0) - self._clock())

    def clear(self, account_id) -> None:
        self._until.pop(account_id, None)


# legacy name from runner.sender — same signature, kept as an alias so
# old imports and old tests keep working unchanged
AccountCooldown = FloodWaitCooldown


class TelegramRateLimitGate:
    """The unified gate: one sliding-window bucket per operation
    category (accounts isolated inside each bucket) plus a shared
    account cooldown breaker that dominates every bucket."""

    DEFAULTS = {
        #  category    gap-env-name                     gap   cap-env-name                    cap
        "send":    ("NEXUS_BRIDGE_SEND_MIN_GAP_S",    3.0, "NEXUS_BRIDGE_SEND_PER_MINUTE",    20),
        "history": ("NEXUS_BRIDGE_HISTORY_MIN_GAP_S", 1.5, "NEXUS_BRIDGE_HISTORY_PER_MINUTE", 30),
        "resolve": ("NEXUS_BRIDGE_RESOLVE_MIN_GAP_S", 2.0, "NEXUS_BRIDGE_RESOLVE_PER_MINUTE", 12),
        "media":   ("NEXUS_BRIDGE_MEDIA_MIN_GAP_S",   0.25, "NEXUS_BRIDGE_MEDIA_PER_MINUTE",  90),
    }

    def __init__(self, clock=None, categories: dict | None = None):
        self._clock = clock or time.monotonic
        overrides = categories or {}
        self._gates: dict = {}
        for category, (gap_env, gap_default, cap_env, cap_default) in self.DEFAULTS.items():
            if category in overrides:
                gap, cap = overrides[category]
            else:
                gap = _env_float(gap_env, gap_default)
                cap = int(_env_float(cap_env, cap_default))
            self._gates[category] = SlidingWindowGate(
                category, gap, cap, clock=self._clock,
            )
        self.cooldown = FloodWaitCooldown(clock=self._clock)

    def gate(self, category: str) -> SlidingWindowGate:
        return self._gates[category]

    def delay_for(self, category: str, account_id) -> float:
        """Total seconds before this account may make the next call of
        this category — the cooldown breaker dominates the bucket."""
        breaker = self.cooldown.remaining(account_id)
        return max(breaker, self._gates[category].delay_for(account_id))

    def record(self, category: str, account_id) -> None:
        self._gates[category].record(account_id)

    def trip_cooldown(self, account_id, seconds) -> float:
        return self.cooldown.trip(account_id, seconds)

    def cooldown_remaining(self, account_id) -> float:
        return self.cooldown.remaining(account_id)

    async def wait(self, category: str, account_id, sleep, stop=None) -> float:
        """Await the gate, then record the call. `sleep` is injected
        (asyncio.sleep in prod, a no-op in tests); `stop` is an
        optional asyncio.Event that shortens the pause so shutdowns
        stay responsive. Returns the waited seconds."""
        delay = self.delay_for(category, account_id)
        if delay > 0 and stop is not None:
            from asyncio import wait_for

            try:
                await wait_for(stop.wait(), timeout=delay)
                return delay  # stop fired — caller should bail out
            except TimeoutError:
                pass
        elif delay > 0:
            await sleep(delay)
        self.record(category, account_id)
        return delay


# process-wide gate shared by sender / backfill (single asyncio process)
GATE = TelegramRateLimitGate()


# backwards-compatible wrapper matching the original runner.sender
# signature exactly (send bucket, env-configurable, injectable clock)
class SendRateLimiter(SlidingWindowGate):
    def __init__(self, min_gap_s=None, per_minute=None, clock=None):
        min_gap = (
            float(min_gap_s) if min_gap_s is not None
            else _env_float("NEXUS_BRIDGE_SEND_MIN_GAP_S", 3.0)
        )
        cap = (
            int(per_minute) if per_minute is not None
            else int(_env_float("NEXUS_BRIDGE_SEND_PER_MINUTE", 20))
        )
        super().__init__("send", min_gap, cap, clock=clock)
