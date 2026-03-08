"""Tests for the correction engine."""

from pathlib import Path

import pytest

from src.config import PipelineConfig
from src.correction_engine import (
    _parse_loudnorm_json,
    build_correction_command,
)
from src.qc_engine import CheckResult, QCReport


@pytest.fixture
def config() -> PipelineConfig:
    """Create a test pipeline config."""
    return PipelineConfig(
        pipeline_root=Path("."),
        loudness_target_lufs=-23.0,
        loudness_target_lra=7.0,
        loudness_target_tp=-1.0,
    )


@pytest.fixture
def failing_report() -> QCReport:
    """Create a QC report with failing checks."""
    return QCReport(
        filename="test.mp4",
        filepath="/path/test.mp4",
        overall_status="FAIL",
        checks={
            "broadcast_legality": CheckResult(status="FAIL"),
            "interlacing": CheckResult(status="FAIL"),
            "audio_loudness": CheckResult(status="FAIL"),
            "black_frames": CheckResult(status="PASS"),
            "frozen_frames": CheckResult(status="PASS"),
        },
        correctable=["broadcast_legality", "interlacing", "audio_loudness"],
    )


class TestBuildCorrectionCommand:
    """Tests for correction command construction."""

    def test_includes_limiter_for_broadcast_fail(self, config, failing_report):
        """Test that limiter filter is added for broadcast legality failure."""
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), failing_report, config,
        )
        vf_idx = cmd.index("-vf")
        vf_string = cmd[vf_idx + 1]
        assert "limiter=min=16:max=235" in vf_string

    def test_includes_bwdif_for_interlacing(self, config, failing_report):
        """Test that bwdif deinterlacing filter is added for interlacing failure."""
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), failing_report, config,
        )
        vf_idx = cmd.index("-vf")
        vf_string = cmd[vf_idx + 1]
        assert "bwdif" in vf_string

    def test_includes_loudnorm(self, config, failing_report):
        """Test that loudnorm filter is added for audio failure."""
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), failing_report, config,
        )
        af_idx = cmd.index("-af")
        af_string = cmd[af_idx + 1]
        assert "loudnorm" in af_string

    def test_loudnorm_with_measured_values(self, config, failing_report):
        """Test that measured values from pass 1 are used in the command."""
        measured = {
            "input_i": "-18.2",
            "input_lra": "5.3",
            "input_tp": "0.3",
            "input_thresh": "-28.5",
        }
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), failing_report, config,
            loudnorm_measured=measured,
        )
        af_idx = cmd.index("-af")
        af_string = cmd[af_idx + 1]
        assert "measured_I=-18.2" in af_string

    def test_no_deinterlace_when_progressive(self, config):
        """Test that bwdif is NOT added when content is progressive."""
        report = QCReport(
            filename="test.mp4",
            filepath="/path/test.mp4",
            overall_status="FAIL",
            checks={
                "broadcast_legality": CheckResult(status="FAIL"),
                "interlacing": CheckResult(status="PASS"),
            },
            correctable=["broadcast_legality"],
        )
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), report, config,
        )
        vf_idx = cmd.index("-vf")
        vf_string = cmd[vf_idx + 1]
        assert "bwdif" not in vf_string

    def test_output_is_prores_10bit(self, config, failing_report):
        """Test that output codec is ProRes 422 HQ 10-bit."""
        cmd = build_correction_command(
            Path("in.mp4"), Path("out.mov"), failing_report, config,
        )
        assert "prores_ks" in cmd
        assert "yuv422p10le" in cmd


class TestParseLoudnormJson:
    """Tests for loudnorm JSON parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid loudnorm JSON from FFmpeg stderr."""
        stderr = """
[Parsed_loudnorm_0 @ 0x1234] {
    "input_i" : "-18.23",
    "input_tp" : "0.30",
    "input_lra" : "5.30",
    "input_thresh" : "-28.50",
    "output_i" : "-23.00",
    "output_tp" : "-1.00",
    "output_lra" : "7.00",
    "output_thresh" : "-33.00",
    "normalization_type" : "dynamic",
    "target_offset" : "0.00"
}
"""
        result = _parse_loudnorm_json(stderr)
        assert result is not None
        assert result["input_i"] == "-18.23"
        assert result["input_tp"] == "0.30"

    def test_parse_no_json(self):
        """Test parsing stderr with no JSON content."""
        result = _parse_loudnorm_json("no json here")
        assert result is None
