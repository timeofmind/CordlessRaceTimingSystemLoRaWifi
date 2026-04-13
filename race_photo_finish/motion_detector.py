"""
motion_detector.py — Detects athlete crossings on the scan column.

Algorithm
---------
1. **Background model** — during a configurable warm-up period (the first
   BACKGROUND_WARMUP_FRAMES columns) the detector accumulates columns and
   computes a per-pixel mean.  This becomes the *background reference*.

2. **Change detection** — for every subsequent column we compute the
   per-pixel absolute difference against the background.  If more than
   MOTION_PIXEL_FRACTION × column_height pixels exceed MOTION_PIXEL_THRESHOLD
   the column is classified as "motion".

3. **Debounce** — a crossing event is only fired if at least
   CROSSING_DEBOUNCE_S seconds have elapsed since the last event.  This
   prevents a single wide athlete from generating dozens of timestamps.

4. **Callback** — on each confirmed crossing the detector calls every
   registered callback with the elapsed race time (seconds from race start).

Design notes
------------
* The detector is intentionally *stateless with respect to timing* — it
  receives the elapsed time from the caller rather than reading a clock
  itself.  This makes it trivial to substitute a LoRa-synced clock later.
* The background model is fixed after the warm-up phase.  A more
  sophisticated system would use a running mean, but a fixed background is
  simpler, faster, and sufficient for the short duration of a race.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Optional

import numpy as np

import config

log = logging.getLogger(__name__)

# Type alias for crossing callbacks: (elapsed_seconds: float) -> None
CrossingCallback = Callable[[float], None]


class MotionDetector:
    """
    Per-column motion detector for photo-finish crossing detection.

    Parameters
    ----------
    column_height : int
        Number of pixels in each scan column.
    pixel_threshold : int
        Absolute per-pixel difference (0–255) required to count as "changed".
    pixel_fraction : float
        Fraction of column pixels that must be "changed" to declare motion.
    debounce_s : float
        Minimum seconds between successive crossing events.
    warmup_frames : int
        Number of frames used to build the background model.
    """

    def __init__(
        self,
        column_height:   int   = config.SENSOR_HEIGHT,
        pixel_threshold: int   = config.MOTION_PIXEL_THRESHOLD,
        pixel_fraction:  float = config.MOTION_PIXEL_FRACTION,
        debounce_s:      float = config.CROSSING_DEBOUNCE_S,
        warmup_frames:   int   = config.BACKGROUND_WARMUP_FRAMES,
    ) -> None:
        self._height          = column_height
        self._pixel_threshold = pixel_threshold
        self._pixel_fraction  = pixel_fraction
        self._debounce_s      = debounce_s
        self._warmup_frames   = warmup_frames

        # Background: accumulated as float32 for precision, compared as uint8.
        self._bg_accumulator: np.ndarray = np.zeros(
            (column_height, 3), dtype=np.float64
        )
        self._background:   Optional[np.ndarray] = None  # (H, 3) uint8
        self._warmup_count: int = 0
        self._ready:       bool = False

        # Debounce state.
        self._last_crossing_wall: float = -1e9   # wall-clock time (monotonic)

        # Registered callbacks — called on every confirmed crossing.
        self._callbacks: list[CrossingCallback] = []

        # Statistics (useful for tuning thresholds).
        self.total_motion_columns:   int = 0
        self.total_crossing_events:  int = 0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_callback(self, cb: CrossingCallback) -> None:
        """
        Register a function to be called on each confirmed crossing.

        Parameters
        ----------
        cb : callable
            Signature: ``cb(elapsed_seconds: float) -> None``
            ``elapsed_seconds`` is measured from race start.
        """
        self._callbacks.append(cb)
        log.debug("Crossing callback registered: %s", cb)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def process_column(
        self,
        column:          np.ndarray,
        elapsed_seconds: float,
    ) -> bool:
        """
        Process one scan column and fire a callback if a crossing is detected.

        Parameters
        ----------
        column : np.ndarray
            Shape (H, 3) — BGR pixels from the scan line.
        elapsed_seconds : float
            Seconds elapsed since the race started (from the timing module).
            The detector does not own a clock; the caller provides this value.

        Returns
        -------
        bool
            True if a *new* crossing event was fired this call.
        """
        if column.shape != (self._height, 3):
            raise ValueError(
                f"Expected column (H={self._height}, 3), got {column.shape}"
            )

        # ----------------------------------------------------------------
        # Phase 1 — warm-up: build the background model.
        # ----------------------------------------------------------------
        if not self._ready:
            self._bg_accumulator += column.astype(np.float64)
            self._warmup_count   += 1

            if self._warmup_count >= self._warmup_frames:
                # Finalise the background as a uint8 mean image.
                self._background = (
                    self._bg_accumulator / self._warmup_count
                ).astype(np.uint8)
                self._ready = True
                log.info(
                    "Background model ready after %d frames.", self._warmup_count
                )
            return False   # No detection during warm-up.

        # ----------------------------------------------------------------
        # Phase 2 — detection: compare column to background.
        # ----------------------------------------------------------------
        # Convert to grayscale for a single-channel diff (faster, sufficient).
        col_gray = self._bgr_to_gray(column)
        bg_gray  = self._bgr_to_gray(self._background)

        diff = np.abs(col_gray.astype(np.int16) - bg_gray.astype(np.int16))
        changed_pixels = int(np.sum(diff > self._pixel_threshold))
        changed_fraction = changed_pixels / self._height

        is_motion = changed_fraction >= self._pixel_fraction

        if is_motion:
            self.total_motion_columns += 1

        # ----------------------------------------------------------------
        # Phase 3 — debounce and event firing.
        # ----------------------------------------------------------------
        if not is_motion:
            return False

        now = time.monotonic()
        if (now - self._last_crossing_wall) < self._debounce_s:
            # Too soon after the last event — suppress.
            return False

        # Confirmed crossing.
        self._last_crossing_wall = now
        self.total_crossing_events += 1

        log.info(
            "CROSSING detected at %.3f s  "
            "(changed pixels: %d / %d = %.1f %%)",
            elapsed_seconds,
            changed_pixels,
            self._height,
            changed_fraction * 100,
        )

        for cb in self._callbacks:
            try:
                cb(elapsed_seconds)
            except Exception:  # noqa: BLE001
                log.exception("Error in crossing callback %s", cb)

        return True

    # ------------------------------------------------------------------
    # Background management
    # ------------------------------------------------------------------

    def reset_background(self) -> None:
        """
        Discard the current background and restart the warm-up phase.

        Call this if lighting conditions change significantly mid-session.
        """
        self._bg_accumulator[:] = 0
        self._background = None
        self._warmup_count = 0
        self._ready = False
        log.info("Background model reset — re-running warm-up.")

    def update_background(self, column: np.ndarray, alpha: float = 0.05) -> None:
        """
        Blend a new column into the background with a small learning rate.

        Optional — call this periodically between races to adapt to slow
        illumination changes (e.g. clouds passing over the finish line).

        Parameters
        ----------
        column : np.ndarray
            Shape (H, 3) — the column to blend in.
        alpha : float
            Learning rate in [0, 1].  0 = no update; 1 = replace entirely.
        """
        if self._background is None:
            return
        bg_f = self._background.astype(np.float32)
        col_f = column.astype(np.float32)
        self._background = np.clip(
            bg_f * (1.0 - alpha) + col_f * alpha, 0, 255
        ).astype(np.uint8)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True once the background warm-up phase is complete."""
        return self._ready

    @property
    def warmup_progress(self) -> float:
        """Fraction of warm-up frames captured (0.0 – 1.0)."""
        return min(self._warmup_count / max(self._warmup_frames, 1), 1.0)

    @property
    def background(self) -> Optional[np.ndarray]:
        """The current background reference column (H, 3) uint8, or None."""
        return self._background

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bgr_to_gray(bgr: np.ndarray) -> np.ndarray:
        """
        Convert a (H, 3) BGR column to a (H,) uint8 luminance column.

        Uses the standard Rec. 601 luminance coefficients.
        """
        b = bgr[:, 0].astype(np.float32)
        g = bgr[:, 1].astype(np.float32)
        r = bgr[:, 2].astype(np.float32)
        return np.clip(0.114 * b + 0.587 * g + 0.299 * r, 0, 255).astype(np.uint8)
