"""
line_scan_builder.py — Constructs the photo-finish composite image.

How line-scan photo finish works
---------------------------------
A traditional film-based photo finish camera exposes a single vertical slit
onto a continuously moving strip of film.  Objects stationary relative to
the ground smear horizontally; objects moving at the finish velocity appear
sharp.  We replicate this digitally:

    • For every captured frame we extract a single vertical column of pixels
      (the "slit") at the configured x-coordinate (SCAN_COLUMN).
    • We concatenate those columns left-to-right.
    • The result is an image whose x-axis represents *time* and whose y-axis
      represents *vertical position at the finish line*.

An athlete crossing at frame N appears as a (nearly) undistorted silhouette
because their feet-to-head extent is fully captured in that single column.

Responsibilities
----------------
* Accept one BGR column-array per call and append it to the composite.
* Expose the current composite as a numpy array at any time.
* Optionally write the composite to disk (PNG) on demand.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np

import config

log = logging.getLogger(__name__)


class LineScanBuilder:
    """
    Accumulates vertical pixel columns into a growing composite image.

    The composite is stored as a pre-allocated ring-buffer that is trimmed
    to the logical width on every read, keeping memory use bounded and
    avoiding repeated numpy concatenation (which would be O(n²)).

    Parameters
    ----------
    column_height : int
        Height of each column in pixels (= frame height).
    max_width : int
        Maximum number of columns to retain (= MAX_COMPOSITE_WIDTH from config).
    """

    def __init__(
        self,
        column_height: int = config.SENSOR_HEIGHT,
        max_width:     int = config.MAX_COMPOSITE_WIDTH,
    ) -> None:
        self._height    = column_height
        self._max_width = max_width

        # Pre-allocate the full buffer once.  Columns are written left-to-right;
        # _write_ptr wraps around when the buffer is full (ring behaviour).
        # Shape: (height, max_width, 3) — BGR uint8.
        self._buffer = np.zeros((column_height, max_width, 3), dtype=np.uint8)

        self._write_ptr:   int = 0   # next column to overwrite
        self._total_cols:  int = 0   # total columns ever appended
        self._is_full:    bool = False

        # Ensure the output directory exists.
        Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def append_column(self, column: np.ndarray) -> None:
        """
        Append one vertical column to the composite.

        Parameters
        ----------
        column : np.ndarray
            Shape (H, 3) — a single column of BGR pixels extracted from a
            frame at x = SCAN_COLUMN.  H must equal ``column_height``.
        """
        if column.ndim != 2 or column.shape[1] != 3:
            raise ValueError(
                f"Expected column shape (H, 3), got {column.shape}"
            )
        if column.shape[0] != self._height:
            raise ValueError(
                f"Column height {column.shape[0]} != expected {self._height}"
            )

        self._buffer[:, self._write_ptr, :] = column
        self._write_ptr = (self._write_ptr + 1) % self._max_width
        self._total_cols += 1

        if not self._is_full and self._total_cols >= self._max_width:
            self._is_full = True
            log.warning(
                "Line-scan buffer full (%d columns). Oldest frames are now "
                "being overwritten. Consider increasing MAX_COMPOSITE_WIDTH.",
                self._max_width,
            )

    def extract_column(self, frame: np.ndarray) -> np.ndarray:
        """
        Extract the configured vertical column from a full frame.

        Parameters
        ----------
        frame : np.ndarray
            Full BGR frame from the camera (H × W × 3).

        Returns
        -------
        np.ndarray
            Shape (H, 3) — single column of BGR pixels.
        """
        col_x = config.SCAN_COLUMN
        if col_x >= frame.shape[1]:
            raise ValueError(
                f"SCAN_COLUMN {col_x} is outside frame width {frame.shape[1]}"
            )
        # frame[:, col_x, :] returns a view; .copy() makes it independent of
        # the camera DMA buffer (which picamera2 may reuse immediately).
        return frame[:, col_x, :].copy()

    def append_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Convenience method: extract the column from *frame* and append it.

        Returns
        -------
        np.ndarray
            The extracted column (H, 3) — useful for passing to the motion
            detector without re-extracting it.
        """
        column = self.extract_column(frame)
        self.append_column(column)
        return column

    # ------------------------------------------------------------------
    # Read-back
    # ------------------------------------------------------------------

    @property
    def composite(self) -> np.ndarray:
        """
        The current composite image as an (H × W × 3) BGR ndarray.

        If the buffer has never been full, W = total columns appended.
        If the buffer has wrapped, W = max_width and the image is ordered
        oldest → newest left to right.
        """
        if not self._is_full:
            # Buffer partially filled — return the valid prefix.
            return self._buffer[:, : self._total_cols, :].copy()
        else:
            # Buffer is full and wrapping.  The oldest column is at _write_ptr.
            # Reassemble in chronological order.
            left  = self._buffer[:, self._write_ptr :, :]
            right = self._buffer[:, : self._write_ptr, :]
            return np.concatenate([left, right], axis=1)

    @property
    def total_columns(self) -> int:
        """Total number of columns appended (may exceed max_width)."""
        return self._total_cols

    @property
    def current_width(self) -> int:
        """Width of the composite image (≤ max_width)."""
        return min(self._total_cols, self._max_width)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, filename: str | None = None) -> Path:
        """
        Write the current composite to disk as a PNG file.

        Parameters
        ----------
        filename : str, optional
            Output filename (relative to OUTPUT_DIR).  Defaults to a
            timestamp-based name.

        Returns
        -------
        Path
            Absolute path of the saved file.
        """
        if filename is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"finish_{ts}.png"

        out_path = Path(config.OUTPUT_DIR) / filename
        composite = self.composite

        if composite.shape[1] == 0:
            log.warning("Composite is empty — nothing to save.")
            return out_path

        success = cv2.imwrite(str(out_path), composite)
        if success:
            log.info(
                "Composite saved → %s  (%d × %d px)",
                out_path, composite.shape[1], composite.shape[0],
            )
        else:
            log.error("cv2.imwrite failed for %s", out_path)

        return out_path

    # ------------------------------------------------------------------
    # Debug / preview helpers
    # ------------------------------------------------------------------

    def preview_window(self, max_display_width: int = 1280) -> None:
        """
        Show a live OpenCV window with the growing composite.

        Call this from a secondary thread or after every N frames.
        Press 'q' to close.
        """
        composite = self.composite
        if composite.shape[1] == 0:
            return

        # Scale down for display if wider than the screen.
        if composite.shape[1] > max_display_width:
            scale  = max_display_width / composite.shape[1]
            new_h  = int(composite.shape[0] * scale)
            display = cv2.resize(composite, (max_display_width, new_h))
        else:
            display = composite

        cv2.imshow("Photo Finish — Line Scan", display)
        cv2.waitKey(1)
