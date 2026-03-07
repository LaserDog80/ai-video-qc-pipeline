"""Tests for the report generator."""

import json
import tempfile
from pathlib import Path

from src.report_generator import generate_html_report, generate_json_report


def _sample_clips() -> list[dict]:
    """Create sample clip data for report testing."""
    return [
        {
            "filename": "clip_001.mp4",
            "duration": 4.5,
            "resolution": "1920x1080",
            "frame_rate": 24.0,
            "codec": "h264",
            "overall_status": "PASS",
            "checks": {
                "broadcast_legality": {
                    "status": "PASS",
                    "illegal_frame_count": 0,
                    "timecodes": [],
                },
                "black_frames": {
                    "status": "PASS",
                    "count": 0,
                    "timecodes": [],
                },
            },
            "correctable": [],
            "requires_manual_review": [],
        },
        {
            "filename": "clip_002.mp4",
            "duration": 3.2,
            "resolution": "1920x1080",
            "frame_rate": 24.0,
            "codec": "h264",
            "overall_status": "FAIL",
            "checks": {
                "broadcast_legality": {
                    "status": "FAIL",
                    "illegal_frame_count": 12,
                    "timecodes": ["00:00:01;12"],
                },
                "audio_loudness": {
                    "status": "FAIL",
                    "integrated_lufs": -18.2,
                    "target_lufs": -23.0,
                    "timecodes": [],
                },
            },
            "correctable": ["broadcast_legality", "audio_loudness"],
            "requires_manual_review": [],
        },
    ]


class TestJsonReport:
    """Tests for JSON report generation."""

    def test_generates_valid_json(self):
        """Test that a valid JSON report is generated."""
        clips = _sample_clips()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "report.json"
            result = generate_json_report(clips, "batch_001", "test_project", output)

            assert result.exists()
            with open(result) as f:
                data = json.load(f)

            assert data["batch_id"] == "batch_001"
            assert data["project"] == "test_project"
            assert data["total_clips"] == 2
            assert data["pass_count"] == 1
            assert data["fail_count"] == 1
            assert len(data["clips"]) == 2

    def test_includes_corrections(self):
        """Test that corrections are included when provided."""
        clips = _sample_clips()
        corrections = [{"filename": "clip_002.mp4", "corrections_applied": ["broadcast_legality_clamp"]}]
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "report.json"
            generate_json_report(clips, "batch_001", "test", output, corrections=corrections)

            with open(output) as f:
                data = json.load(f)
            assert "corrections" in data


class TestHtmlReport:
    """Tests for HTML report generation."""

    def test_generates_html_file(self):
        """Test that an HTML report file is generated."""
        clips = _sample_clips()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "report.html"
            result = generate_html_report(clips, "batch_001", "test_project", output)

            assert result.exists()
            content = result.read_text()
            assert "<!DOCTYPE html>" in content
            assert "batch_001" in content
            assert "clip_001.mp4" in content
            assert "clip_002.mp4" in content
            assert "PASS" in content
            assert "FAIL" in content
