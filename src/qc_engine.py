"""QC Analysis Engine for the AI Video QC Pipeline.

Builds FFmpeg commands to analyse video clips, parses the diagnostic output,
and produces structured per-clip QC reports.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import PipelineConfig, QCThresholds

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single QC check."""

    status: str  # "PASS", "WARN", or "FAIL"
    details: dict = field(default_factory=dict)
    timecodes: list = field(default_factory=list)


@dataclass
class QCReport:
    """Complete QC report for a single clip."""

    filename: str
    filepath: str
    duration: float = 0.0
    resolution: str = ""
    frame_rate: float = 0.0
    codec: str = ""
    bit_depth: int = 8
    colour_space: str = ""
    overall_status: str = "PASS"
    checks: dict = field(default_factory=dict)
    correctable: list = field(default_factory=list)
    requires_manual_review: list = field(default_factory=list)


def get_media_info(filepath: Path) -> dict:
    """Extract media metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(filepath),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        logger.error("ffprobe failed for %s: %s", filepath, e)
        return {}


def build_qc_command(
    filepath: Path,
    thresholds: QCThresholds,
    hwaccel: Optional[str] = None,
) -> list[str]:
    """Build the FFmpeg QC analysis command.

    Constructs a single-pass command with all analysis filters running
    in parallel on decoded frames.
    """
    bl = thresholds.broadcast_legality
    bf = thresholds.black_frames
    ff = thresholds.frozen_frames
    fl = thresholds.flash_frames
    sil = thresholds.silence
    crop = thresholds.crop_letterbox

    video_filters = [
        "signalstats=stat=tout+vrep+brng,metadata=mode=print",
        f"blackdetect=d={bf.get('black_min_duration', 0.04)}"
        f":pix_th={bf.get('pixel_threshold', 0.10)}",
        f"freezedetect=n={ff.get('noise_threshold', 0.001)}"
        f":d={ff.get('min_duration', 0.08)}",
        f"cropdetect=round={crop.get('cropdetect_round', 2)}",
        f"scdet=threshold={fl.get('scene_score_threshold', 0.4)}",
        "idet",
    ]

    audio_filters = [
        "ebur128=metadata=1",
        f"silencedetect=noise={sil.get('noise_threshold_db', -50)}dB"
        f":d={sil.get('min_duration', 0.5)}",
    ]

    cmd = ["ffmpeg", "-hide_banner"]

    if hwaccel:
        cmd.extend(["-hwaccel", hwaccel])

    cmd.extend([
        "-i", str(filepath),
        "-vf", ",".join(video_filters),
        "-af", ",".join(audio_filters),
        "-f", "null", "-",
    ])

    return cmd


def run_qc_analysis(
    filepath: Path,
    config: PipelineConfig,
    thresholds: QCThresholds,
) -> QCReport:
    """Run QC analysis on a single clip and return a structured report."""
    filepath = Path(filepath)
    logger.info("Starting QC analysis: %s", filepath.name)

    # Get media info first
    media_info = get_media_info(filepath)
    probe_format = media_info.get("format", {})
    video_stream = next(
        (s for s in media_info.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (s for s in media_info.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )

    report = QCReport(
        filename=filepath.name,
        filepath=str(filepath),
        duration=float(probe_format.get("duration", 0)),
        resolution=f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}",
        frame_rate=_parse_frame_rate(video_stream.get("r_frame_rate", "0/1")),
        codec=video_stream.get("codec_name", "unknown"),
        bit_depth=int(video_stream.get("bits_per_raw_sample", 8) or 8),
        colour_space=video_stream.get("color_space", "unknown"),
    )

    # Build and run FFmpeg QC command
    cmd = build_qc_command(filepath, thresholds, config.hwaccel)
    logger.debug("QC command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        stderr_output = result.stderr
    except subprocess.TimeoutExpired:
        logger.error("QC analysis timed out for %s", filepath.name)
        report.overall_status = "ERROR"
        return report

    # Parse FFmpeg output into check results
    has_audio = bool(audio_stream)
    report.checks = parse_qc_output(stderr_output, thresholds, has_audio)

    # Determine overall status and correctable issues
    _evaluate_report(report)

    logger.info(
        "QC complete: %s — %s", filepath.name, report.overall_status,
    )
    return report


def parse_qc_output(
    stderr: str,
    thresholds: QCThresholds,
    has_audio: bool = True,
) -> dict[str, CheckResult]:
    """Parse FFmpeg stderr output into structured check results."""
    checks = {}
    checks["broadcast_legality"] = _parse_broadcast_legality(stderr, thresholds)
    checks["black_frames"] = _parse_black_frames(stderr, thresholds)
    checks["frozen_frames"] = _parse_frozen_frames(stderr, thresholds)
    checks["flash_frames"] = _parse_flash_frames(stderr, thresholds)
    checks["interlacing"] = _parse_interlacing(stderr, thresholds)
    checks["crop_letterbox"] = _parse_crop_letterbox(stderr, thresholds)

    if has_audio:
        checks["audio_loudness"] = _parse_audio_loudness(stderr, thresholds)
        checks["silence"] = _parse_silence(stderr, thresholds)

    return checks


def _parse_broadcast_legality(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse signalstats output for broadcast legality."""
    bl = thresholds.broadcast_legality
    ymin_legal = bl.get("ymin_legal", 16)
    ymax_legal = bl.get("ymax_legal", 235)

    ymin_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.YMIN=(\d+)", stderr)]
    ymax_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.YMAX=(\d+)", stderr)]
    umin_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.UMIN=(\d+)", stderr)]
    umax_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.UMAX=(\d+)", stderr)]
    vmin_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.VMIN=(\d+)", stderr)]
    vmax_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.VMAX=(\d+)", stderr)]
    brng_values = [int(m) for m in re.findall(r"lavfi\.signalstats\.BRNG=(\d+)", stderr)]

    illegal_frames = sum(1 for v in ymin_values if v < ymin_legal)
    illegal_frames += sum(1 for v in ymax_values if v > ymax_legal)
    total_brng = sum(brng_values)

    worst_ymin = min(ymin_values) if ymin_values else ymin_legal
    worst_ymax = max(ymax_values) if ymax_values else ymax_legal

    if illegal_frames > bl.get("max_illegal_frames", 0) or total_brng > 0:
        status = "FAIL"
    elif worst_ymin <= bl.get("warn_ymin", 18) or worst_ymax >= bl.get("warn_ymax", 232):
        status = "WARN"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "illegal_frame_count": illegal_frames,
            "total_brng_pixels": total_brng,
            "worst_ymin": worst_ymin,
            "worst_ymax": worst_ymax,
            "frames_analysed": len(ymin_values),
        },
    )


def _parse_black_frames(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse blackdetect output."""
    pattern = r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
    matches = re.findall(pattern, stderr)

    detections = []
    total_duration = 0.0
    for start, end, duration in matches:
        total_duration += float(duration)
        detections.append({
            "start": float(start),
            "end": float(end),
            "duration": float(duration),
        })

    timecodes = [_seconds_to_timecode(d["start"]) for d in detections]

    if detections:
        status = "FAIL"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "detections": detections,
            "total_black_duration": round(total_duration, 3),
            "count": len(detections),
        },
        timecodes=timecodes,
    )


def _parse_frozen_frames(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse freezedetect output."""
    ff = thresholds.frozen_frames
    starts = re.findall(r"lavfi\.freezedetect\.freeze_start:\s*([\d.]+)", stderr)
    durations = re.findall(r"lavfi\.freezedetect\.freeze_duration:\s*([\d.]+)", stderr)
    ends = re.findall(r"lavfi\.freezedetect\.freeze_end:\s*([\d.]+)", stderr)

    detections = []
    for i, start in enumerate(starts):
        dur = float(durations[i]) if i < len(durations) else 0.0
        end = float(ends[i]) if i < len(ends) else float(start) + dur
        detections.append({
            "start": float(start),
            "end": end,
            "duration": dur,
        })

    timecodes = [_seconds_to_timecode(d["start"]) for d in detections]
    max_dur = max((d["duration"] for d in detections), default=0.0)

    if max_dur >= ff.get("max_freeze_duration", 0.5):
        status = "FAIL"
    elif detections:
        status = "WARN"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "detections": detections,
            "max_freeze_duration": round(max_dur, 3),
            "count": len(detections),
        },
        timecodes=timecodes,
    )


def _parse_flash_frames(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse scdet (scene change detection) output for flash frames."""
    fl = thresholds.flash_frames
    threshold = fl.get("scene_score_threshold", 0.4)

    # scdet outputs: lavfi.scd.time and lavfi.scd.score
    times = re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", stderr)
    scores = re.findall(r"lavfi\.scd\.score:\s*([\d.]+)", stderr)

    detections = []
    for i, t in enumerate(times):
        score = float(scores[i]) if i < len(scores) else 0.0
        if score >= threshold:
            detections.append({"time": float(t), "score": score})

    timecodes = [_seconds_to_timecode(d["time"]) for d in detections]
    max_allowed = fl.get("max_flash_frames", 0)

    if len(detections) > max_allowed:
        status = "WARN"  # Flash frames are flagged but not auto-correctable
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "detections": detections,
            "count": len(detections),
        },
        timecodes=timecodes,
    )


def _parse_interlacing(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse idet output for interlacing detection."""
    tff_match = re.search(r"TFF:\s*(\d+)", stderr)
    bff_match = re.search(r"BFF:\s*(\d+)", stderr)
    progressive_match = re.search(r"Progressive:\s*(\d+)", stderr)
    undetermined_match = re.search(r"Undetermined:\s*(\d+)", stderr)

    tff = int(tff_match.group(1)) if tff_match else 0
    bff = int(bff_match.group(1)) if bff_match else 0
    progressive = int(progressive_match.group(1)) if progressive_match else 0
    undetermined = int(undetermined_match.group(1)) if undetermined_match else 0

    interlaced_count = tff + bff
    max_allowed = thresholds.interlacing.get("max_interlaced_frames", 0)

    if interlaced_count > max_allowed:
        status = "FAIL"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "tff_frames": tff,
            "bff_frames": bff,
            "progressive_frames": progressive,
            "undetermined_frames": undetermined,
            "interlaced_total": interlaced_count,
        },
    )


def _parse_audio_loudness(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse ebur128 output for audio loudness compliance."""
    al = thresholds.audio_loudness
    target = al.get("target_lufs", -23.0)
    tolerance = al.get("lufs_tolerance", 1.0)
    max_tp = al.get("max_true_peak_dbtp", -1.0)

    # ebur128 summary values
    integrated_match = re.search(r"I:\s*([-\d.]+)\s*LUFS", stderr)
    lra_match = re.search(r"LRA:\s*([-\d.]+)\s*LU", stderr)
    tp_match = re.search(r"Peak:\s*([-\d.]+)\s*dBFS", stderr)
    # Alternative true peak pattern
    if not tp_match:
        tp_match = re.search(r"True peak:\s*([-\d.]+)", stderr)

    integrated = float(integrated_match.group(1)) if integrated_match else None
    lra = float(lra_match.group(1)) if lra_match else None
    true_peak = float(tp_match.group(1)) if tp_match else None

    if integrated is None:
        return CheckResult(
            status="WARN",
            details={"error": "Could not parse loudness data — no audio or parse failure"},
        )

    lufs_ok = abs(integrated - target) <= tolerance
    tp_ok = true_peak is None or true_peak <= max_tp

    if lufs_ok and tp_ok:
        status = "PASS"
    else:
        status = "FAIL"

    return CheckResult(
        status=status,
        details={
            "integrated_lufs": integrated,
            "loudness_range_lu": lra,
            "true_peak_dbtp": true_peak,
            "target_lufs": target,
            "tolerance": tolerance,
        },
    )


def _parse_silence(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse silencedetect output."""
    sil = thresholds.silence
    pattern = r"silence_start:\s*([\d.]+).*?silence_end:\s*([\d.]+).*?silence_duration:\s*([\d.]+)"
    matches = re.findall(pattern, stderr, re.DOTALL)

    detections = []
    total_duration = 0.0
    for start, end, duration in matches:
        total_duration += float(duration)
        detections.append({
            "start": float(start),
            "end": float(end),
            "duration": float(duration),
        })

    timecodes = [_seconds_to_timecode(d["start"]) for d in detections]

    max_dur = sil.get("max_silence_duration", 5.0)
    warn_dur = sil.get("warn_silence_duration", 2.0)

    if total_duration >= max_dur:
        status = "FAIL"
    elif total_duration >= warn_dur:
        status = "WARN"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "detections": detections,
            "total_silence_duration": round(total_duration, 3),
            "count": len(detections),
        },
        timecodes=timecodes,
    )


def _parse_crop_letterbox(stderr: str, thresholds: QCThresholds) -> CheckResult:
    """Parse cropdetect output for letterboxing inconsistencies."""
    crop_values = re.findall(r"crop=(\d+:\d+:\d+:\d+)", stderr)

    if not crop_values:
        return CheckResult(status="PASS", details={"note": "No crop data detected"})

    unique_crops = set(crop_values)
    max_variance = thresholds.crop_letterbox.get("max_crop_variance", 4)

    # Parse crop dimensions to check for variance
    widths = set()
    heights = set()
    for crop in unique_crops:
        parts = crop.split(":")
        widths.add(int(parts[0]))
        heights.add(int(parts[1]))

    width_variance = max(widths) - min(widths) if widths else 0
    height_variance = max(heights) - min(heights) if heights else 0

    if width_variance > max_variance or height_variance > max_variance:
        status = "WARN"
    else:
        status = "PASS"

    return CheckResult(
        status=status,
        details={
            "unique_crop_values": list(unique_crops),
            "width_variance": width_variance,
            "height_variance": height_variance,
            "dominant_crop": max(set(crop_values), key=crop_values.count),
        },
    )


def _evaluate_report(report: QCReport) -> None:
    """Set overall status, correctable list, and manual review list."""
    correctable_checks = {"broadcast_legality", "audio_loudness", "interlacing", "silence"}
    manual_review_checks = {"frozen_frames", "flash_frames", "crop_letterbox"}

    worst = "PASS"
    for name, check in report.checks.items():
        if isinstance(check, dict):
            status = check.get("status", "PASS")
        else:
            status = check.status

        if status == "FAIL":
            worst = "FAIL"
            if name in correctable_checks:
                report.correctable.append(name)
            if name in manual_review_checks:
                report.requires_manual_review.append(name)
        elif status == "WARN" and worst != "FAIL":
            worst = "WARN"
            if name in manual_review_checks:
                report.requires_manual_review.append(name)

    report.overall_status = worst


def extract_thumbnail(
    filepath: Path,
    timecode: str,
    output_path: Path,
    quality: int = 2,
) -> Optional[Path]:
    """Extract a single frame as JPEG at the given timecode."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # FFmpeg -ss doesn't accept drop-frame semicolons — convert to colons
    seek_time = timecode.replace(";", ":")
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-ss", seek_time,
        "-i", str(filepath),
        "-frames:v", "1",
        "-q:v", str(quality),
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.warning("Thumbnail extraction failed at %s: %s", timecode, e)
        return None


def report_to_dict(report: QCReport) -> dict:
    """Convert a QCReport to a JSON-serialisable dictionary."""
    checks_dict = {}
    for name, check in report.checks.items():
        if isinstance(check, CheckResult):
            checks_dict[name] = {
                "status": check.status,
                **check.details,
                "timecodes": check.timecodes,
            }
        else:
            checks_dict[name] = check

    return {
        "filename": report.filename,
        "filepath": report.filepath,
        "duration": report.duration,
        "resolution": report.resolution,
        "frame_rate": report.frame_rate,
        "codec": report.codec,
        "bit_depth": report.bit_depth,
        "colour_space": report.colour_space,
        "overall_status": report.overall_status,
        "checks": checks_dict,
        "correctable": report.correctable,
        "requires_manual_review": report.requires_manual_review,
    }


def _parse_frame_rate(rate_str: str) -> float:
    """Parse ffprobe frame rate string like '24000/1001' to float."""
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/")
            return round(float(num) / float(den), 3)
        return float(rate_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _seconds_to_timecode(seconds: float, fps: float = 25.0) -> str:
    """Convert seconds to HH:MM:SS;FF timecode string."""
    total_frames = int(seconds * fps)
    frames = total_frames % int(fps)
    total_seconds = int(seconds)
    secs = total_seconds % 60
    mins = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{mins:02d}:{secs:02d};{frames:02d}"
