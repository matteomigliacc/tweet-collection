"""Monitor backend failures that can checkpoint empty months without raising errors."""
import asyncio
import re
import time
from collections import Counter
from datetime import datetime

from loguru import logger

import notifications

# "API unknown error: 200 - 235/500 - account4 - (-1) DeadlineExceeded: Unspecified"
ERROR_RE = re.compile(r"\(-1\)\s*(\w+)")
# "No account available for queue "SearchTimeline". Next available at 09:28:43"
WAIT_RE = re.compile(r"No account available for queue \"?(\w+)")

# Errors that mean "the query failed and returned nothing".
DATA_LOSS_KINDS = frozenset({"Dependency", "DeadlineExceeded", "OverCapacity",
                             "Unavailable", "TimeoutError", "ServiceUnavailable"})


class ErrorMonitor:
    """Counts backend errors on the loguru stream and posts periodic Teams cards."""

    def __init__(self, window_minutes: float = 10.0, spike_threshold: int = 25):
        self.window_seconds = max(30.0, window_minutes * 60.0)
        self.spike_threshold = spike_threshold
        self.window: Counter[str] = Counter()
        self.totals: Counter[str] = Counter()
        self.waits = 0
        self.total_waits = 0
        self.current_target = "—"
        self.spikes = 0
        self.started = time.monotonic()
        self._sink_id: int | None = None
        self._task: asyncio.Task | None = None

    # -- collection ------------------------------------------------------

    def record(self, text: str) -> None:
        """Classify one log line. Never raises."""
        try:
            m = ERROR_RE.search(text)
            if m:
                kind = m.group(1)
                self.window[kind] += 1
                self.totals[kind] += 1
                return
            if WAIT_RE.search(text):
                self.waits += 1
                self.total_waits += 1
        except Exception:
            pass

    def _sink(self, message) -> None:
        try:
            text = message.record["message"]
        except Exception:
            text = str(message)
        self.record(text)

    def install(self) -> None:
        """Attach to the loguru stream twscrape logs into."""
        if self._sink_id is None:
            self._sink_id = logger.add(self._sink, level="INFO", format="{message}")

    def remove(self) -> None:
        if self._sink_id is not None:
            try:
                logger.remove(self._sink_id)
            except Exception:
                pass
            self._sink_id = None

    # -- reporting -------------------------------------------------------

    @property
    def errors_total(self) -> int:
        return sum(self.totals.values())

    @property
    def data_loss_total(self) -> int:
        return sum(n for k, n in self.totals.items() if k in DATA_LOSS_KINDS)

    def take_window(self) -> tuple[Counter, int]:
        """Return (errors, waits) for the window just elapsed and reset it."""
        errs, waits = self.window, self.waits
        self.window, self.waits = Counter(), 0
        return errs, waits

    def build_card(self, errs: Counter, waits: int) -> dict:
        n = sum(errs.values())
        spike = n >= self.spike_threshold
        mins = self.window_seconds / 60.0
        elapsed = (time.monotonic() - self.started) / 60.0
        if spike:
            title = f"🛑 Error spike — {n} failed queries in {mins:.0f} min"
            note = ("X's backend is refusing queries. Failed queries return **zero "
                    "tweets** and the month is still checkpointed as done, so this "
                    "run is losing data. Stop it and re-collect these handles later.")
            colour = "Attention"
        else:
            title = f"⚠️ {n} failed quer{'y' if n == 1 else 'ies'} in the last {mins:.0f} min"
            note = ("Low error rate — some months may have come back empty. Worth a "
                    "recall check against the reference data afterwards.")
            colour = "Warning"
        facts = [{"title": k, "value": str(v)} for k, v in errs.most_common()]
        facts.append({"title": "rate-limit waits", "value": f"{waits} (benign)"})
        facts.append({"title": "errors this run", "value": str(self.errors_total)})
        return {
            "type": "AdaptiveCard", "version": "1.4",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": [
                {"type": "TextBlock", "weight": "Bolder", "size": "Medium",
                 "color": colour, "wrap": True, "text": title},
                {"type": "TextBlock", "wrap": True, "spacing": "None",
                 "text": f"while collection **{self.current_target}** · "
                         f"{elapsed:.0f} min into the run"},
                {"type": "FactSet", "facts": facts},
                {"type": "TextBlock", "wrap": True, "text": note},
                {"type": "TextBlock", "size": "Small", "isSubtle": True,
                 "spacing": "None", "text": f"{datetime.now():%H:%M} · collect monitor"},
            ],
        }

    def report_now(self) -> bool:
        """Post the current window if it holds any errors. Returns True if posted."""
        errs, waits = self.take_window()
        if not errs:
            return False
        if sum(errs.values()) >= self.spike_threshold:
            self.spikes += 1
        try:
            return notifications.send_teams(self.build_card(errs, waits))
        except Exception as e:
            logger.warning(f"error-monitor card failed ({e!r})")
            return False

    def final_summary(self) -> str:
        """One line for the end-of-run console output and session card."""
        if not self.errors_total:
            return f"collect clean — 0 backend errors, {self.total_waits} rate-limit waits"
        kinds = ", ".join(f"{k} {v}" for k, v in self.totals.most_common(4))
        warn = "  ** months may have been checkpointed empty **" if self.data_loss_total else ""
        return (f"{self.errors_total} backend errors ({kinds}), "
                f"{self.total_waits} waits, {self.spikes} spike(s){warn}")

    # -- lifecycle -------------------------------------------------------

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.window_seconds)
                self.report_now()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"error monitor stopped ({e!r})")

    def start(self) -> None:
        self.install()
        try:
            self._task = asyncio.get_running_loop().create_task(self._loop())
        except RuntimeError:
            self._task = None  # no loop (sync context) — sink still collects

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.report_now()   # flush whatever the last partial window held
        self.remove()


if __name__ == "__main__":  # self-test: python src/collection_errors.py  (no Teams post)
    real_lines = [
        'API unknown error: 200 -  6/50 - BroodjeHarpjmb - (-1) Dependency: Unspecified',
        'API unknown error: 200 - 235/500 - account4 - (-1) DeadlineExceeded: Unspecified',
        'API unknown error: 200 - 12/50 - HokeyPokeyfs - (-1) OverCapacity: Unspecified',
        'No account available for queue "SearchTimeline". Next available at 09:28:43',
        'Teams card posted (HTTP 202)',
    ]
    m = ErrorMonitor(window_minutes=10, spike_threshold=3)
    m.current_target = "SGP/SGPnieuws"
    for line in real_lines * 2:
        m.record(line)

    assert m.totals["Dependency"] == 2, m.totals
    assert m.totals["DeadlineExceeded"] == 2, m.totals
    assert m.totals["OverCapacity"] == 2, m.totals
    assert m.total_waits == 2, m.total_waits
    assert m.errors_total == 6, m.errors_total
    assert m.data_loss_total == 6, m.data_loss_total

    errs, waits = m.take_window()
    card = m.build_card(errs, waits)
    assert m.window == Counter() and m.waits == 0, "window not reset"
    assert card["body"][0]["color"] == "Attention", "6 errors >= threshold 3 -> spike"
    print("counts OK:", dict(m.totals), f"waits={m.total_waits}")
    print("card title:", card["body"][0]["text"])
    print("summary:", m.final_summary())

    quiet = ErrorMonitor()
    for line in ['No account available for queue "SearchTimeline". Next available at 09:28',
                 'Teams card posted (HTTP 202)']:
        quiet.record(line)
    assert quiet.errors_total == 0
    assert quiet.report_now() is False, "a clean window must post nothing"
    print("summary:", quiet.final_summary())
    print("\nself-test passed")
