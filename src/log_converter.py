"""Standard Log Conversion Engine for the AI Video QC Pipeline.

Applies a Rec.709-to-log .cube LUT via FFmpeg to produce 10-bit ProRes 422 HQ
output with correct colour metadata tags.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from src.config import PipelineConfig

logger = logging.getLogger(__name__)


def build_log_conversion_command(
    input_path: Path,
    output_path: Path,
    lut_path: Path,
    config: PipelineConfig,
) -> list[str]:
    """Build the FFmpeg LUT application command for standard log conversion."""
    cmd = ["ffmpeg", "-hide_banner", "-y"]

    if config.hwaccel:
        cmd.extend(["-hwaccel", config.hwaccel])

    cmd.extend([
        "-i", str(input_path),
        "-vf", f"lut3d={lut_path}",
        "-c:v", "prores_ks", "-profile:v", "3",
        "-pix_fmt", config.output_pixel_format,
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-color_range", "tv",
        "-c:a", "copy",
        str(output_path),
    ])

    return cmd


def build_combined_correction_and_log_command(
    input_path: Path,
    output_path: Path,
    lut_path: Path,
    video_filters: list[str],
    audio_filters: list[str],
    config: PipelineConfig,
) -> list[str]:
    """Build a single-pass command that applies corrections and LUT together.

    For the standard tier, this eliminates an unnecessary decode/encode cycle
    by combining correction filters and the LUT into one filtergraph.
    """
    # Add LUT as the final video filter
    all_video_filters = video_filters + [f"lut3d={lut_path}"]

    cmd = ["ffmpeg", "-hide_banner", "-y"]

    if config.hwaccel:
        cmd.extend(["-hwaccel", config.hwaccel])

    cmd.extend(["-i", str(input_path)])

    if all_video_filters:
        cmd.extend(["-vf", ",".join(all_video_filters)])

    if audio_filters:
        cmd.extend(["-af", ",".join(audio_filters)])
    else:
        cmd.extend(["-c:a", "copy"])

    cmd.extend([
        "-c:v", "prores_ks", "-profile:v", "3",
        "-pix_fmt", config.output_pixel_format,
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-color_range", "tv",
        str(output_path),
    ])

    return cmd


def run_log_conversion(
    input_path: Path,
    output_dir: Path,
    config: PipelineConfig,
    lut_path: Optional[Path] = None,
) -> Optional[Path]:
    """Apply standard Rec.709-to-log conversion to a clip.

    Returns the output file path on success, None on failure.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if lut_path is None:
        lut_path = Path(config.pipeline_root) / config.lut_standard

    if not lut_path.exists():
        logger.error("LUT file not found: %s", lut_path)
        return None

    output_path = output_dir / f"{input_path.stem}_log.mov"

    cmd = build_log_conversion_command(input_path, output_path, lut_path, config)
    logger.info("Running log conversion: %s", input_path.name)
    logger.debug("Command: %s", " ".join(cmd))

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
        logger.info("Log conversion complete: %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(
            "Log conversion failed for %s: %s",
            input_path.name,
            e.stderr[-500:] if e.stderr else str(e),
        )
        return None
    except subprocess.TimeoutExpired:
        logger.error("Log conversion timed out for %s", input_path.name)
        return None


def validate_lut_file(lut_path: Path) -> bool:
    """Basic validation that a .cube LUT file is readable."""
    lut_path = Path(lut_path)
    if not lut_path.exists():
        logger.error("LUT file does not exist: %s", lut_path)
        return False

    if lut_path.suffix.lower() != ".cube":
        logger.error("LUT file is not a .cube file: %s", lut_path)
        return False

    try:
        with open(lut_path) as f:
            header = f.read(1024)
        if "LUT_3D_SIZE" not in header:
            logger.warning("LUT file may be invalid (no LUT_3D_SIZE header): %s", lut_path)
            return False
    except OSError as e:
        logger.error("Cannot read LUT file %s: %s", lut_path, e)
        return False

    return True
