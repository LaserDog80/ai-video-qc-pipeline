"""Automated Correction Engine for the AI Video QC Pipeline.

Builds FFmpeg commands to fix correctable issues identified by the QC engine.
Supports broadcast legality clamping, loudness normalisation (two-pass),
deinterlacing, and frame rate conversion.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import PipelineConfig
from src.qc_engine import QCReport

logger = logging.getLogger(__name__)


@dataclass
class CorrectionLog:
    """Record of corrections applied to a clip."""

    filename: str
    corrections_applied: list = field(default_factory=list)
    loudnorm_measured: Optional[dict] = None
    command: str = ""
    success: bool = False
    output_path: str = ""


def build_correction_command(
    input_path: Path,
    output_path: Path,
    qc_report: QCReport,
    config: PipelineConfig,
    loudnorm_measured: Optional[dict] = None,
) -> list[str]:
    """Build the FFmpeg correction command based on QC results.

    Filters are applied in order: colour clamping, deinterlacing,
    frame rate, then audio correction.
    """
    video_filters = []
    audio_filters = []
    corrections = []

    checks = qc_report.checks

    # 1. Broadcast legality — limiter to clamp luma/chroma
    bl_check = checks.get("broadcast_legality")
    if bl_check and _get_status(bl_check) == "FAIL":
        video_filters.append("limiter=min=16:max=235:planes=1")
        corrections.append("broadcast_legality_clamp")

    # 2. Deinterlacing — only if interlacing was detected
    idet_check = checks.get("interlacing")
    if idet_check and _get_status(idet_check) == "FAIL":
        video_filters.append("bwdif=mode=send_frame:parity=auto")
        corrections.append("deinterlace")

    # 3. Frame rate conversion if needed
    if config.frame_rate_target:
        video_filters.append(f"fps={config.frame_rate_target}")

    # 4. Audio loudness normalisation (pass 2 with measured values)
    audio_check = checks.get("audio_loudness")
    if audio_check and _get_status(audio_check) in ("FAIL", "WARN"):
        if loudnorm_measured:
            audio_filters.append(
                f"loudnorm=I={config.loudness_target_lufs}"
                f":LRA={config.loudness_target_lra}"
                f":TP={config.loudness_target_tp}"
                f":measured_I={loudnorm_measured['input_i']}"
                f":measured_LRA={loudnorm_measured['input_lra']}"
                f":measured_TP={loudnorm_measured['input_tp']}"
                f":measured_thresh={loudnorm_measured['input_thresh']}"
                f":print_format=json"
            )
        else:
            audio_filters.append(
                f"loudnorm=I={config.loudness_target_lufs}"
                f":LRA={config.loudness_target_lra}"
                f":TP={config.loudness_target_tp}"
                f":print_format=json"
            )
        corrections.append("audio_loudness_normalise")

    cmd = ["ffmpeg", "-hide_banner", "-y"]

    if config.hwaccel:
        cmd.extend(["-hwaccel", config.hwaccel])

    cmd.extend(["-i", str(input_path)])

    if video_filters:
        cmd.extend(["-vf", ",".join(video_filters)])

    if audio_filters:
        cmd.extend(["-af", ",".join(audio_filters)])

    # Output codec: ProRes 422 HQ, 10-bit
    cmd.extend([
        "-c:v", "prores_ks", "-profile:v", "3",
        "-pix_fmt", config.output_pixel_format,
        "-c:a", "pcm_s24le",
        str(output_path),
    ])

    return cmd


def run_loudnorm_pass1(
    input_path: Path,
    config: PipelineConfig,
) -> Optional[dict]:
    """Run loudnorm analysis pass (pass 1) to measure audio levels.

    Returns the measured values needed for the accurate pass 2 correction.
    """
    logger.info("Running loudnorm pass 1 (analysis): %s", input_path.name)

    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(input_path),
        "-af", f"loudnorm=I={config.loudness_target_lufs}"
               f":LRA={config.loudness_target_lra}"
               f":TP={config.loudness_target_tp}"
               f":print_format=json",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return _parse_loudnorm_json(result.stderr)
    except subprocess.TimeoutExpired:
        logger.error("Loudnorm pass 1 timed out for %s", input_path.name)
        return None


def _parse_loudnorm_json(stderr: str) -> Optional[dict]:
    """Extract the loudnorm JSON output from FFmpeg stderr."""
    # Find the JSON block output by loudnorm
    json_start = stderr.rfind("{")
    json_end = stderr.rfind("}") + 1

    if json_start == -1 or json_end == 0:
        logger.warning("Could not find loudnorm JSON in FFmpeg output")
        return None

    try:
        return json.loads(stderr[json_start:json_end])
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse loudnorm JSON: %s", e)
        return None


def run_correction(
    input_path: Path,
    output_dir: Path,
    qc_report: QCReport,
    config: PipelineConfig,
) -> CorrectionLog:
    """Run the full correction pipeline on a clip.

    Performs two-pass loudness normalisation if needed, then applies
    all corrections in a single FFmpeg pass.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{input_path.stem}_corrected.mov"
    log = CorrectionLog(filename=input_path.name, output_path=str(output_path))

    # Check if any corrections are needed
    if not qc_report.correctable:
        logger.info("No corrections needed for %s", input_path.name)
        log.success = True
        log.output_path = str(input_path)  # Use original
        return log

    # Two-pass loudness if audio correction needed
    loudnorm_measured = None
    audio_check = qc_report.checks.get("audio_loudness")
    if audio_check and _get_status(audio_check) in ("FAIL", "WARN"):
        loudnorm_measured = run_loudnorm_pass1(input_path, config)
        log.loudnorm_measured = loudnorm_measured

    # Build and run correction command
    cmd = build_correction_command(
        input_path, output_path, qc_report, config, loudnorm_measured,
    )
    log.command = " ".join(cmd)
    log.corrections_applied = [
        c for c in _detect_corrections(qc_report)
    ]

    logger.info("Applying corrections to %s: %s", input_path.name, log.corrections_applied)

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
        log.success = True
        logger.info("Correction complete: %s", output_path.name)
    except subprocess.CalledProcessError as e:
        logger.error("Correction failed for %s: %s", input_path.name, e.stderr[-500:] if e.stderr else str(e))
        log.success = False
    except subprocess.TimeoutExpired:
        logger.error("Correction timed out for %s", input_path.name)
        log.success = False

    return log


def _detect_corrections(qc_report: QCReport) -> list[str]:
    """List the corrections that will be applied based on QC results."""
    corrections = []
    checks = qc_report.checks

    if _get_status(checks.get("broadcast_legality")) == "FAIL":
        corrections.append("broadcast_legality_clamp")
    if _get_status(checks.get("interlacing")) == "FAIL":
        corrections.append("deinterlace")
    if _get_status(checks.get("audio_loudness")) in ("FAIL", "WARN"):
        corrections.append("audio_loudness_normalise")

    return corrections


def _get_status(check) -> str:
    """Get status from a check result (handles both dict and CheckResult)."""
    if check is None:
        return "PASS"
    if isinstance(check, dict):
        return check.get("status", "PASS")
    return getattr(check, "status", "PASS")


def correction_log_to_dict(log: CorrectionLog) -> dict:
    """Convert a CorrectionLog to a JSON-serialisable dictionary."""
    return {
        "filename": log.filename,
        "corrections_applied": log.corrections_applied,
        "loudnorm_measured": log.loudnorm_measured,
        "command": log.command,
        "success": log.success,
        "output_path": log.output_path,
    }
