#!/usr/bin/env python3
"""AI Video QC Pipeline — Main Orchestrator.

Coordinates all pipeline stages: QC analysis, correction, and log conversion.
Run from the command line with a batch path as argument.

Usage:
    python pipeline_orchestrator.py --batch input/project/batch_001/
    python pipeline_orchestrator.py --batch input/project/batch_001/ --qc-only
    python pipeline_orchestrator.py --batch input/project/batch_001/ --tier standard --auto-correct
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import (
    PipelineConfig,
    QCThresholds,
    ensure_directories,
    load_pipeline_config,
    load_qc_thresholds,
)
from src.correction_engine import correction_log_to_dict, run_correction
from src.log_converter import run_log_conversion, validate_lut_file
from src.qc_engine import (
    QCReport,
    extract_thumbnail,
    report_to_dict,
    run_qc_analysis,
)
from src.report_generator import generate_html_report, generate_json_report

logger = logging.getLogger("pipeline")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".ts"}


def setup_logging(log_dir: Path) -> None:
    """Configure logging to both file and console."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"pipeline_{timestamp}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )


def discover_clips(batch_dir: Path) -> list[Path]:
    """Find all video files in a batch directory."""
    clips = sorted(
        p for p in batch_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    return clips


def run_pipeline(
    batch_dir: Path,
    config: PipelineConfig,
    thresholds: QCThresholds,
    qc_only: bool = False,
    auto_correct: bool = True,
    tier: str = "standard",
) -> dict:
    """Run the full pipeline on a batch of clips.

    Returns a summary dict with counts and output paths.
    """
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        logger.error("Batch directory not found: %s", batch_dir)
        sys.exit(1)

    # Derive project and batch names from path
    parts = batch_dir.parts
    batch_id = parts[-1] if len(parts) >= 1 else "unknown"
    project = parts[-2] if len(parts) >= 2 else "unknown"

    root = config.pipeline_root
    ensure_directories(root)

    clips = discover_clips(batch_dir)
    if not clips:
        logger.warning("No video files found in %s", batch_dir)
        return {"total": 0, "error": "No video files found"}

    logger.info(
        "Pipeline starting — Project: %s, Batch: %s, Clips: %d, Tier: %s",
        project, batch_id, len(clips), tier,
    )

    # Stage 1: QC Analysis
    qc_reports: list[QCReport] = []
    qc_dicts: list[dict] = []

    for clip_path in clips:
        report = run_qc_analysis(clip_path, config, thresholds)
        qc_reports.append(report)
        qc_dicts.append(report_to_dict(report))

        # Extract thumbnails for failed/warned checks
        if config.extract_thumbnails:
            _extract_report_thumbnails(clip_path, report, root, batch_id)

        # Write per-clip status checkpoint
        status_file = root / "staging" / "qc_analysed" / f"{clip_path.stem}.qc_complete"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.touch()

    # Generate QC reports
    reports_dir = root / "reports"
    generate_json_report(
        qc_dicts, batch_id, project,
        reports_dir / f"{batch_id}_qc_report.json",
    )
    generate_html_report(
        qc_dicts, batch_id, project,
        reports_dir / f"{batch_id}_qc_report.html",
    )

    summary = {
        "total": len(clips),
        "pass": sum(1 for r in qc_reports if r.overall_status == "PASS"),
        "warn": sum(1 for r in qc_reports if r.overall_status == "WARN"),
        "fail": sum(1 for r in qc_reports if r.overall_status == "FAIL"),
        "qc_report_json": str(reports_dir / f"{batch_id}_qc_report.json"),
        "qc_report_html": str(reports_dir / f"{batch_id}_qc_report.html"),
    }

    if qc_only:
        logger.info("QC-only mode — stopping after analysis.")
        _print_summary(summary)
        return summary

    # Stage 2: Correction (if enabled)
    correction_logs = []
    corrected_paths: dict[str, Path] = {}

    if auto_correct:
        corrected_dir = root / "staging" / "corrected" / project / batch_id
        for i, clip_path in enumerate(clips):
            report = qc_reports[i]
            if report.correctable:
                log = run_correction(clip_path, corrected_dir, report, config)
                correction_logs.append(correction_log_to_dict(log))
                if log.success and log.output_path != str(clip_path):
                    corrected_paths[clip_path.name] = Path(log.output_path)

                    # Write checkpoint
                    status_file = (
                        root / "staging" / "corrected"
                        / f"{clip_path.stem}.correction_complete"
                    )
                    status_file.touch()

        # Save correction log
        if correction_logs:
            correction_log_path = reports_dir / f"{batch_id}_correction_log.json"
            with open(correction_log_path, "w") as f:
                json.dump(correction_logs, f, indent=2, default=str)
            summary["correction_log"] = str(correction_log_path)

    # Stage 3: Standard Log Conversion
    if tier in ("standard", "both"):
        lut_path = root / config.lut_standard
        if not lut_path.exists():
            logger.warning(
                "Standard LUT not found at %s — skipping log conversion. "
                "Place a .cube LUT file there to enable this stage.",
                lut_path,
            )
        else:
            output_dir = root / "output" / "standard" / project / batch_id
            converted_count = 0
            for clip_path in clips:
                # Use corrected version if available
                source = corrected_paths.get(clip_path.name, clip_path)
                result = run_log_conversion(source, output_dir, config, lut_path)
                if result:
                    converted_count += 1
            summary["standard_converted"] = converted_count

    # Stage 4: Premium tier — copy to Topaz input (manual step required)
    if tier in ("premium", "both"):
        topaz_input_dir = root / "staging" / "topaz_input" / project / batch_id
        topaz_input_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Premium tier: corrected clips should be loaded into Topaz Video AI "
            "from %s. Run with --watch-topaz to monitor for Topaz output.",
            topaz_input_dir,
        )
        summary["topaz_input_dir"] = str(topaz_input_dir)

    _print_summary(summary)
    return summary


def _extract_report_thumbnails(
    clip_path: Path,
    report: QCReport,
    root: Path,
    batch_id: str,
) -> None:
    """Extract thumbnail frames for any failed or warned checks."""
    thumbs_dir = root / "reports" / "thumbs" / batch_id
    for check_name, check in report.checks.items():
        timecodes = getattr(check, "timecodes", [])
        if not timecodes:
            continue
        status = getattr(check, "status", "PASS")
        if status in ("FAIL", "WARN"):
            for tc in timecodes[:3]:  # Limit to 3 thumbnails per check
                safe_tc = tc.replace(":", "").replace(";", "")
                thumb_path = thumbs_dir / f"{clip_path.stem}_{check_name}_{safe_tc}.jpg"
                extract_thumbnail(clip_path, tc, thumb_path)


def _print_summary(summary: dict) -> None:
    """Print a human-readable pipeline summary."""
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    logger.info("Total clips:   %d", summary.get("total", 0))
    logger.info("  Passed:      %d", summary.get("pass", 0))
    logger.info("  Warnings:    %d", summary.get("warn", 0))
    logger.info("  Failed:      %d", summary.get("fail", 0))
    if "standard_converted" in summary:
        logger.info("  Converted:   %d (standard)", summary["standard_converted"])
    if "qc_report_html" in summary:
        logger.info("HTML report:   %s", summary["qc_report_html"])
    if "qc_report_json" in summary:
        logger.info("JSON report:   %s", summary["qc_report_json"])
    logger.info("=" * 60)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="AI Video QC Pipeline — analyse, correct, and convert AI-generated video",
    )
    parser.add_argument(
        "--batch", required=True,
        help="Path to the batch directory containing video clips",
    )
    parser.add_argument(
        "--qc-only", action="store_true",
        help="Run QC analysis only, do not correct or convert",
    )
    parser.add_argument(
        "--tier", choices=["standard", "premium", "both"], default="standard",
        help="Output tier: standard (FFmpeg LUT), premium (Topaz+Resolve), or both",
    )
    parser.add_argument(
        "--auto-correct", action="store_true", default=None,
        help="Enable automatic correction of fixable issues",
    )
    parser.add_argument(
        "--no-auto-correct", action="store_true",
        help="Disable automatic correction",
    )
    parser.add_argument(
        "--config", default="config/pipeline_config.yaml",
        help="Path to pipeline configuration file",
    )
    parser.add_argument(
        "--thresholds", default="config/qc_thresholds.yaml",
        help="Path to QC thresholds configuration file",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    config = load_pipeline_config(args.config)
    thresholds = load_qc_thresholds(args.thresholds)

    setup_logging(config.pipeline_root / "logs")

    # CLI flags override config
    auto_correct = config.auto_correct
    if args.auto_correct is not None:
        auto_correct = True
    if args.no_auto_correct:
        auto_correct = False

    tier = args.tier or config.output_tier

    run_pipeline(
        batch_dir=Path(args.batch),
        config=config,
        thresholds=thresholds,
        qc_only=args.qc_only,
        auto_correct=auto_correct,
        tier=tier,
    )


if __name__ == "__main__":
    main()
