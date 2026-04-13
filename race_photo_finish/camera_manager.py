"""
camera_manager.py — Wraps picamera2 for maximum-fps capture.

Responsibilities
----------------
* Configure the IMX296 global-shutter sensor for the highest sustainable
  frame rate given the chosen resolution / ROI.
* Expose a simple iterator interface:  ``for frame in camera: …``
* Report the actual achieved frame rate so the timing layer can use it.

This module deliberately knows nothing about line-scanning, motion, or
timing — those concerns live in their own modules.
"""

from __future__ import annotations

import logging
import time
from typing import Generator

import numpy as np

# picamera2 is only available on Raspberry Pi; guard the import so the rest
# of the codebase can be imported (and unit-tested) on a desktop machine.
try:
    from picamera2 import Picamera2
    from libcamera import controls  # type: ignore
    _PICAMERA2_AVAILABLE = True
except ImportError:
    _PICAMERA2_AVAILABLE = False
    logging.warning(
        "picamera2 not found — CameraManager will run in SIMULATION mode."
    )

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class CameraManager:
    """
    Manages the Raspberry Pi Global Shutter Camera (IMX296) via picamera2.

    Usage::

        with CameraManager() as cam:
            for frame in cam.frames():
                process(frame)   # frame is an H×W×3 uint8 BGR ndarray
    """

    def __init__(self) -> None:
        self._camera:    "Picamera2 | None" = None
        self._actual_fps: float = float(config.CAMERA_FPS)
        self._frame_count: int  = 0
        self._start_time: float = 0.0

    # ------------------------------------------------------------------
    # Context-manager helpers
    # ------------------------------------------------------------------

    def __enter__(self) -> "CameraManager":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise and start the camera stream."""
        if not _PICAMERA2_AVAILABLE:
            log.info("Simulation mode: camera not started.")
            return

        log.info("Initialising camera …")
        self._camera = Picamera2()

        # ----------------------------------------------------------------
        # Build the capture configuration.
        #
        # We use the "raw" main stream so that ISP processing (debayering,
        # tone-mapping, etc.) is bypassed and latency is minimised.
        # BGR888 keeps every frame directly usable by OpenCV.
        # ----------------------------------------------------------------
        size = (config.SENSOR_WIDTH, config.SENSOR_HEIGHT)

        main_stream = {
            "format": config.CAMERA_FORMAT,
            "size":   size,
        }
        capture_config = self._camera.create_video_configuration(
            main=main_stream,
            # buffer_count: more buffers → smoother capture at high fps,
            # but more RAM. 4 is a good balance for the Pi 5.
            buffer_count=4,
            # queue=False: don't queue frames; if the consumer is slow,
            # drop rather than accumulate latency.
            queue=False,
        )

        # ----------------------------------------------------------------
        # Region-of-interest crop.
        #
        # ScalerCrop is specified in *sensor* pixel coordinates as
        # (x_offset, y_offset, width, height).  A narrower crop lets the
        # sensor read out fewer lines per frame → higher fps.
        # ----------------------------------------------------------------
        if config.CAMERA_ROI is not None:
            x, y, w, h = config.CAMERA_ROI
            capture_config["controls"] = {
                "ScalerCrop": (x, y, w, h),
            }
            log.info("ROI set to %s", config.CAMERA_ROI)

        self._camera.configure(capture_config)

        # ----------------------------------------------------------------
        # Frame-duration limit → drives the sensor frame rate.
        #
        # FrameDurationLimits = (min_µs, max_µs).  Setting both equal
        # pins the frame period, giving us a stable, predictable fps.
        # ----------------------------------------------------------------
        target_duration_us = int(1_000_000 / config.CAMERA_FPS)
        self._camera.set_controls({
            "FrameDurationLimits": (target_duration_us, target_duration_us),
            # Disable auto-exposure so the sensor doesn't vary the frame
            # duration to hit an exposure target.
            "AeEnable": False,
            # Fix exposure to just under one frame period so there is no
            # motion blur (the global shutter already eliminates rolling
            # artefacts, but a long exposure still blurs fast objects).
            "ExposureTime": max(1000, target_duration_us - 500),
            # Fixed analogue gain — tune for your lighting conditions.
            "AnalogueGain": 1.0,
        })

        self._camera.start()
        self._start_time = time.monotonic()
        log.info(
            "Camera started — target fps: %d, resolution: %dx%d",
            config.CAMERA_FPS, *size,
        )

    def stop(self) -> None:
        """Stop the camera and log the measured frame rate."""
        if self._camera is not None:
            self._camera.stop()
            self._camera.close()
            self._camera = None

        elapsed = time.monotonic() - self._start_time
        if elapsed > 0 and self._frame_count > 0:
            self._actual_fps = self._frame_count / elapsed
            log.info(
                "Camera stopped — captured %d frames in %.1f s (%.1f fps)",
                self._frame_count, elapsed, self._actual_fps,
            )

    # ------------------------------------------------------------------
    # Frame iterator
    # ------------------------------------------------------------------

    def frames(self) -> Generator[np.ndarray, None, None]:
        """
        Yield BGR frames as numpy arrays (H × W × 3, uint8) indefinitely.

        In simulation mode (no picamera2) a synthetic frame is generated
        so the rest of the pipeline can be exercised on a desktop.
        """
        if not _PICAMERA2_AVAILABLE or self._camera is None:
            yield from self._simulated_frames()
            return

        while True:
            # capture_array() blocks until the next frame is ready from
            # the sensor.  It returns a zero-copy view into the DMA buffer
            # so we must NOT hold a reference across the next call — the
            # caller should process or copy the column immediately.
            frame: np.ndarray = self._camera.capture_array("main")
            self._frame_count += 1
            yield frame

    # ------------------------------------------------------------------
    # Simulation (desktop / CI use)
    # ------------------------------------------------------------------

    @staticmethod
    def _simulated_frames() -> Generator[np.ndarray, None, None]:
        """
        Generate synthetic frames at ~60 fps for development / testing.

        The simulation produces a moving vertical stripe to mimic an
        athlete crossing the finish line, so the motion detector has
        something to trigger on.
        """
        h, w = config.SENSOR_HEIGHT, config.SENSOR_WIDTH
        rng   = np.random.default_rng(seed=42)
        frame_idx = 0
        period = 1.0 / config.CAMERA_FPS

        while True:
            t0 = time.monotonic()

            # Base frame: random Gaussian noise simulating a textured
            # outdoor scene under constant illumination.
            frame = rng.integers(60, 130, size=(h, w, 3), dtype=np.uint8)

            # Simulate an athlete crossing after 3 seconds (180 frames).
            if 180 <= frame_idx <= 240:
                # Bright vertical stripe sweeping left-to-right.
                stripe_x = config.SCAN_COLUMN + (frame_idx - 180) * 4
                x0 = max(0, stripe_x - 20)
                x1 = min(w, stripe_x + 20)
                frame[:, x0:x1, :] = 220

            frame_idx += 1
            yield frame

            # Throttle to approximate the target fps.
            elapsed = time.monotonic() - t0
            sleep_s = period - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def actual_fps(self) -> float:
        """Measured fps after the camera has been stopped, else target fps."""
        return self._actual_fps

    @property
    def frame_count(self) -> int:
        """Total frames captured so far."""
        return self._frame_count
