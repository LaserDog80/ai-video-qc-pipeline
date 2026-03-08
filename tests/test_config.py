"""Tests for the configuration loader."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.config import (
    PipelineConfig,
    QCThresholds,
    ensure_directories,
    load_pipeline_config,
    load_qc_thresholds,
)


def test_load_pipeline_config():
    """Test loading the default pipeline config."""
    config = load_pipeline_config("config/pipeline_config.yaml")
    assert isinstance(config, PipelineConfig)
    assert config.auto_correct is True
    assert config.output_tier == "standard"
    assert config.loudness_target_lufs == -23.0
    assert config.frame_rate_target == 25.0


def test_load_qc_thresholds():
    """Test loading the default QC thresholds."""
    thresholds = load_qc_thresholds("config/qc_thresholds.yaml")
    assert isinstance(thresholds, QCThresholds)
    assert thresholds.broadcast_legality["ymin_legal"] == 16
    assert thresholds.broadcast_legality["ymax_legal"] == 235
    assert thresholds.frozen_frames["noise_threshold"] == 0.001
    assert thresholds.audio_loudness["target_lufs"] == -23.0


def test_load_pipeline_config_missing_file():
    """Test that missing config file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_pipeline_config("nonexistent.yaml")


def test_load_qc_thresholds_missing_file():
    """Test that missing thresholds file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_qc_thresholds("nonexistent.yaml")


def test_load_custom_config():
    """Test loading a custom config from a temporary file."""
    custom_config = {
        "pipeline_root": "/tmp/test_pipeline",
        "auto_correct": False,
        "output_tier": "premium",
        "loudness_target_lufs": -24.0,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom_config, f)
        f.flush()
        config = load_pipeline_config(f.name)

    assert config.auto_correct is False
    assert config.output_tier == "premium"
    assert config.loudness_target_lufs == -24.0


def test_ensure_directories():
    """Test that ensure_directories creates all required subdirectories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ensure_directories(root)

        expected = [
            "config/luts", "input", "staging/qc_analysed",
            "staging/corrected", "staging/topaz_input",
            "staging/topaz_output", "output/standard",
            "output/premium", "reports/thumbs", "logs",
        ]
        for d in expected:
            assert (root / d).is_dir(), f"Missing directory: {d}"
