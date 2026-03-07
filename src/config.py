"""Configuration loader for the AI Video QC Pipeline."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class PipelineConfig:
    """Master pipeline configuration."""

    pipeline_root: Path
    auto_correct: bool = True
    output_tier: str = "standard"
    output_codec: str = "prores_ks -profile:v 3"
    output_bit_depth: str = "10"
    output_pixel_format: str = "yuv422p10le"
    frame_rate_target: float = 25.0
    lut_standard: str = "config/luts/rec709_to_log.cube"
    lut_premium: str = "config/luts/hdr_to_log.cube"
    loudness_standard: str = "ebu_r128"
    loudness_target_lufs: float = -23.0
    loudness_target_lra: float = 7.0
    loudness_target_tp: float = -1.0
    resolve_project_name: str = "AI_QC_Pipeline"
    resolve_render_preset: str = "ProRes 422 HQ"
    topaz_watch_interval: int = 30
    hwaccel: Optional[str] = None
    extract_thumbnails: bool = True
    thumbnail_quality: int = 2


@dataclass
class QCThresholds:
    """QC pass/fail threshold configuration."""

    broadcast_legality: dict = field(default_factory=dict)
    black_frames: dict = field(default_factory=dict)
    frozen_frames: dict = field(default_factory=dict)
    flash_frames: dict = field(default_factory=dict)
    interlacing: dict = field(default_factory=dict)
    audio_loudness: dict = field(default_factory=dict)
    silence: dict = field(default_factory=dict)
    crop_letterbox: dict = field(default_factory=dict)


def load_pipeline_config(config_path: str = "config/pipeline_config.yaml") -> PipelineConfig:
    """Load pipeline configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    data["pipeline_root"] = Path(data.get("pipeline_root", "."))
    return PipelineConfig(**data)


def load_qc_thresholds(thresholds_path: str = "config/qc_thresholds.yaml") -> QCThresholds:
    """Load QC threshold configuration from YAML file."""
    path = Path(thresholds_path)
    if not path.exists():
        raise FileNotFoundError(f"Thresholds file not found: {thresholds_path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    return QCThresholds(**data)


def ensure_directories(root: Path) -> None:
    """Create the pipeline directory structure if it doesn't exist."""
    dirs = [
        "config/luts",
        "input",
        "staging/qc_analysed",
        "staging/corrected",
        "staging/topaz_input",
        "staging/topaz_output",
        "output/standard",
        "output/premium",
        "reports/thumbs",
        "logs",
    ]
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
