"""
timing_recorder.py — Race clock and crossing-event log.

Responsibilities
----------------
* Maintain a race start time (set by the main loop, or later by the LoRa
  start-signal module).
* Record crossing events with elapsed times and monotonic wall times.
* Provide a clean interface for the web UI and UDP transmitter (Step 3/4).
* Persist events to a JSON log on disk.

This module owns the *definition* of elapsed time but not the mechanism that
starts the clock.  The main loop (or LoRa module) calls ``start_race()``; the
motion detector calls the ``record_crossing`` method via callback.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CrossingEvent:
    """A single athlete-crossing record."""

    # Sequential crossing number (1-indexed).
    index: int

    # Elapsed race time in seconds (float, millisecond precision).
    elapsed_s: float

    # Formatted display string, e.g. "00:12.345".
    elapsed_str: str

    # Monotonic wall time at the moment of crossing (for post-hoc analysis).
    wall_time: float

    # Column index in the composite image where the crossing was detected.
    composite_column: int = 0


@dataclass
class RaceSession:
    """Aggregates all events for one race run."""

    # ISO-8601 timestamp when the race was started.
    start_iso: str = ""

    # Monotonic start time (not serialisable but used for elapsed calcs).
    _start_monotonic: float = field(default=0.0, repr=False)

    # All detected crossings in order.
    crossings: list[CrossingEvent] = field(default_factory=list)

    @property
    def started(self) -> bool:
        return self._start_monotonic > 0

    def elapsed(self) -> float:
        """Current elapsed time in seconds.  0.0 if race hasn't started."""
        if not self.started:
            return 0.0
        return time.monotonic() - self._start_monotonic

    def to_dict(self) -> dict:
        return {
            "start_iso": self.start_iso,
            "crossings": [asdict(c) for c in self.crossings],
        }


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class TimingRecorder:
    """
    Manages the race clock and records crossing events.

    Intended usage
    --------------
    ::

        recorder = TimingRecorder()

        # Wire the motion detector callback:
        detector.register_callback(recorder.record_crossing)

        # Start the clock (called by LoRa handler in Step 3):
        recorder.start_race()

        # Query elapsed time each frame:
        elapsed = recorder.elapsed_seconds

        # Get all events after the race:
        events = recorder.session.crossings
    """

    def __init__(self) -> None:
        self._session: RaceSession = RaceSession()
        self._composite_column_counter: int = 0  # updated by main loop

        # Auto-start if configured (development / testing convenience).
        if config.AUTO_START_CLOCK:
            log.info("AUTO_START_CLOCK is True — starting race clock now.")
            self.start_race()

    # ------------------------------------------------------------------
    # Race control
    # ------------------------------------------------------------------

    def start_race(self) -> None:
        """
        Start (or restart) the race clock.

        Call this when the starter's pistol fires (or when the LoRa
        start-signal is received in Step 3).
        """
        self._session = RaceSession()
        self._session._start_monotonic = time.monotonic()
        self._session.start_iso = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime()
        )
        log.info("Race started at %s", self._session.start_iso)

    def set_composite_column(self, column: int) -> None:
        """
        Called by the main loop each frame to keep track of the current
        composite column index so it can be embedded in crossing records.
        """
        self._composite_column_counter = column

    # ------------------------------------------------------------------
    # Crossing callback
    # ------------------------------------------------------------------

    def record_crossing(self, elapsed_s: float) -> None:
        """
        Record a crossing event.  Registered as a callback with MotionDetector.

        Parameters
        ----------
        elapsed_s : float
            Elapsed race time in seconds, as measured by the motion detector
            (which gets it from the main loop's call to ``elapsed_seconds``).
        """
        if not self._session.started:
            log.debug("Crossing detected before race start — ignored.")
            return

        index = len(self._session.crossings) + 1
        event = CrossingEvent(
            index            = index,
            elapsed_s        = round(elapsed_s, 4),
            elapsed_str      = self._format(elapsed_s),
            wall_time        = time.monotonic(),
            composite_column = self._composite_column_counter,
        )
        self._session.crossings.append(event)

        log.info(
            "  → Crossing #%d recorded: %s (%.4f s)",
            index, event.elapsed_str, elapsed_s,
        )
        self._autosave()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed race time in seconds.  0.0 if race hasn't started."""
        return self._session.elapsed()

    @property
    def session(self) -> RaceSession:
        """The current race session data."""
        return self._session

    @property
    def race_started(self) -> bool:
        return self._session.started

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_log(self, filename: Optional[str] = None) -> Path:
        """
        Write the session to a JSON file in OUTPUT_DIR.

        Returns the path of the saved file.
        """
        if filename is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"session_{ts}.json"

        out_path = Path(config.OUTPUT_DIR) / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(self._session.to_dict(), fh, indent=2)

        log.info("Session log saved → %s", out_path)
        return out_path

    def _autosave(self) -> None:
        """Save the log automatically after every crossing."""
        try:
            self.save_log("session_current.json")
        except OSError:
            log.exception("Auto-save failed.")

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format(seconds: float) -> str:
        """
        Format elapsed seconds as  MM:SS.mmm

        Examples::

            0.0    →  "00:00.000"
            12.345 →  "00:12.345"
            75.001 →  "01:15.001"
        """
        minutes = int(seconds) // 60
        secs    = seconds - minutes * 60
        return f"{minutes:02d}:{secs:06.3f}"

    def summary(self) -> str:
        """Return a human-readable summary of the session."""
        lines = [
            f"Race start: {self._session.start_iso or 'not started'}",
            f"Crossings:  {len(self._session.crossings)}",
        ]
        for ev in self._session.crossings:
            lines.append(f"  #{ev.index:>3}  {ev.elapsed_str}  (col {ev.composite_column})")
        return "\n".join(lines)
