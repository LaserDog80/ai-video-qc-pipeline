"""Tests for the QC analysis engine."""

from pathlib import Path

import pytest

from src.config import PipelineConfig, QCThresholds, load_qc_thresholds
from src.qc_engine import (
    CheckResult,
    QCReport,
    _parse_frame_rate,
    _seconds_to_timecode,
    build_qc_command,
    parse_qc_output,
    report_to_dict,
)


@pytest.fixture
def thresholds() -> QCThresholds:
    """Load default QC thresholds."""
    return load_qc_thresholds("config/qc_thresholds.yaml")


@pytest.fixture
def config() -> PipelineConfig:
    """Create a test pipeline config."""
    return PipelineConfig(pipeline_root=Path("."))


class TestBuildQCCommand:
    """Tests for QC command construction."""

    def test_basic_command_structure(self, thresholds: QCThresholds):
        """Test that the QC command has the correct structure."""
        cmd = build_qc_command(Path("test.mp4"), thresholds)
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "test.mp4" in cmd
        assert "-vf" in cmd
        assert "-af" in cmd
        assert "-f" in cmd
        assert "null" in cmd

    def test_video_filters_present(self, thresholds: QCThresholds):
        """Test that all required video filters are in the command."""
        cmd = build_qc_command(Path("test.mp4"), thresholds)
        vf_idx = cmd.index("-vf")
        vf_string = cmd[vf_idx + 1]

        assert "signalstats" in vf_string
        assert "blackdetect" in vf_string
        assert "freezedetect" in vf_string
        assert "cropdetect" in vf_string
        assert "scdet" in vf_string
        assert "idet" in vf_string

    def test_audio_filters_present(self, thresholds: QCThresholds):
        """Test that audio filters are in the command."""
        cmd = build_qc_command(Path("test.mp4"), thresholds)
        af_idx = cmd.index("-af")
        af_string = cmd[af_idx + 1]

        assert "ebur128" in af_string
        assert "silencedetect" in af_string

    def test_hwaccel_flag(self, thresholds: QCThresholds):
        """Test hardware acceleration flag is added when specified."""
        cmd = build_qc_command(Path("test.mp4"), thresholds, hwaccel="cuda")
        assert "-hwaccel" in cmd
        assert "cuda" in cmd


class TestParseQCOutput:
    """Tests for FFmpeg stderr parsing."""

    def test_parse_broadcast_legality_pass(self, thresholds: QCThresholds):
        """Test parsing signalstats output that passes."""
        stderr = (
            "lavfi.signalstats.YMIN=20\n"
            "lavfi.signalstats.YMAX=220\n"
            "lavfi.signalstats.UMIN=30\n"
            "lavfi.signalstats.UMAX=200\n"
            "lavfi.signalstats.VMIN=30\n"
            "lavfi.signalstats.VMAX=200\n"
            "lavfi.signalstats.BRNG=0\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["broadcast_legality"].status == "PASS"

    def test_parse_broadcast_legality_fail(self, thresholds: QCThresholds):
        """Test parsing signalstats output that fails."""
        stderr = (
            "lavfi.signalstats.YMIN=4\n"
            "lavfi.signalstats.YMAX=248\n"
            "lavfi.signalstats.UMIN=10\n"
            "lavfi.signalstats.UMAX=245\n"
            "lavfi.signalstats.VMIN=10\n"
            "lavfi.signalstats.VMAX=245\n"
            "lavfi.signalstats.BRNG=150\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["broadcast_legality"].status == "FAIL"
        assert checks["broadcast_legality"].details["worst_ymin"] == 4
        assert checks["broadcast_legality"].details["worst_ymax"] == 248

    def test_parse_black_frames_detected(self, thresholds: QCThresholds):
        """Test parsing blackdetect output with detections."""
        stderr = (
            "[blackdetect @ 0x1234] black_start:1.5 black_end:2.0 black_duration:0.5\n"
            "[blackdetect @ 0x1234] black_start:5.0 black_end:5.08 black_duration:0.08\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["black_frames"].status == "FAIL"
        assert checks["black_frames"].details["count"] == 2

    def test_parse_frozen_frames(self, thresholds: QCThresholds):
        """Test parsing freezedetect output."""
        stderr = (
            "lavfi.freezedetect.freeze_start: 3.0\n"
            "lavfi.freezedetect.freeze_duration: 0.12\n"
            "lavfi.freezedetect.freeze_end: 3.12\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["frozen_frames"].status == "WARN"
        assert checks["frozen_frames"].details["count"] == 1

    def test_parse_scene_changes(self, thresholds: QCThresholds):
        """Test parsing scdet output."""
        stderr = (
            "lavfi.scd.time: 2.5\n"
            "lavfi.scd.score: 0.85\n"
            "lavfi.scd.time: 4.1\n"
            "lavfi.scd.score: 0.62\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["flash_frames"].status == "WARN"
        assert checks["flash_frames"].details["count"] == 2

    def test_parse_interlacing_progressive(self, thresholds: QCThresholds):
        """Test parsing idet output for progressive content."""
        stderr = (
            "Multi frame detection: TFF:    0 BFF:    0 "
            "Progressive:  250 Undetermined:    0\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["interlacing"].status == "PASS"

    def test_parse_interlacing_detected(self, thresholds: QCThresholds):
        """Test parsing idet output with interlaced frames."""
        stderr = (
            "Multi frame detection: TFF:   45 BFF:    0 "
            "Progressive:  200 Undetermined:    5\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=False)
        assert checks["interlacing"].status == "FAIL"
        assert checks["interlacing"].details["tff_frames"] == 45

    def test_parse_audio_loudness(self, thresholds: QCThresholds):
        """Test parsing ebur128 summary output."""
        stderr = (
            "Summary:\n"
            "  Integrated loudness:\n"
            "    I:         -18.2 LUFS\n"
            "    Threshold: -28.5 LUFS\n"
            "  Loudness range:\n"
            "    LRA:         5.3 LU\n"
            "  True peak:\n"
            "    Peak:        0.3 dBFS\n"
        )
        checks = parse_qc_output(stderr, thresholds, has_audio=True)
        assert checks["audio_loudness"].status == "FAIL"
        assert checks["audio_loudness"].details["integrated_lufs"] == -18.2

    def test_parse_no_output(self, thresholds: QCThresholds):
        """Test parsing empty stderr output gives no failures."""
        checks = parse_qc_output("", thresholds, has_audio=False)
        # With no data, broadcast legality defaults trigger WARN
        # (worst_ymin defaults to 16 which is <= warn_ymin 18)
        assert checks["broadcast_legality"].status in ("PASS", "WARN")
        assert checks["black_frames"].status == "PASS"


class TestUtilities:
    """Tests for utility functions."""

    def test_frame_rate_parsing(self):
        """Test ffprobe frame rate string parsing."""
        assert _parse_frame_rate("24000/1001") == pytest.approx(23.976, abs=0.001)
        assert _parse_frame_rate("25/1") == 25.0
        assert _parse_frame_rate("30") == 30.0
        assert _parse_frame_rate("0/1") == 0.0

    def test_timecode_conversion(self):
        """Test seconds to timecode conversion."""
        assert _seconds_to_timecode(0.0) == "00:00:00;00"
        assert _seconds_to_timecode(1.5) == "00:00:01;12"
        assert _seconds_to_timecode(61.0) == "00:01:01;00"
        assert _seconds_to_timecode(3661.0) == "01:01:01;00"

    def test_report_to_dict(self):
        """Test QCReport serialisation to dict."""
        report = QCReport(
            filename="test.mp4",
            filepath="/path/test.mp4",
            duration=4.5,
            resolution="1920x1080",
            frame_rate=24.0,
            codec="h264",
            overall_status="PASS",
            checks={
                "broadcast_legality": CheckResult(
                    status="PASS",
                    details={"illegal_frame_count": 0},
                ),
            },
        )
        d = report_to_dict(report)
        assert d["filename"] == "test.mp4"
        assert d["overall_status"] == "PASS"
        assert d["checks"]["broadcast_legality"]["status"] == "PASS"
