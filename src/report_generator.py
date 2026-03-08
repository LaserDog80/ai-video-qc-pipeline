"""Report Generator for the AI Video QC Pipeline.

Produces HTML and JSON reports from QC analysis and correction results.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.qc_engine import QCReport, report_to_dict

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QC Report — {{ batch_id }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #1a1a2e; color: #e0e0e0; padding: 2rem; }
        h1 { color: #fff; margin-bottom: 0.5rem; }
        .subtitle { color: #888; margin-bottom: 2rem; font-size: 0.9rem; }
        .summary-bar { display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }
        .stat-card { background: #16213e; border-radius: 8px; padding: 1rem 1.5rem;
                     min-width: 140px; }
        .stat-card .label { font-size: 0.75rem; text-transform: uppercase;
                            color: #888; letter-spacing: 0.05em; }
        .stat-card .value { font-size: 1.8rem; font-weight: 700; margin-top: 0.25rem; }
        .pass { color: #00c853; }
        .warn { color: #ffab00; }
        .fail { color: #ff1744; }
        .clip-card { background: #16213e; border-radius: 8px; padding: 1.5rem;
                     margin-bottom: 1rem; border-left: 4px solid #333; }
        .clip-card.status-PASS { border-left-color: #00c853; }
        .clip-card.status-WARN { border-left-color: #ffab00; }
        .clip-card.status-FAIL { border-left-color: #ff1744; }
        .clip-header { display: flex; justify-content: space-between; align-items: center;
                       margin-bottom: 1rem; }
        .clip-name { font-size: 1.1rem; font-weight: 600; }
        .clip-meta { font-size: 0.8rem; color: #888; }
        .badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px;
                 font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
        .badge-pass { background: rgba(0,200,83,0.15); color: #00c853; }
        .badge-warn { background: rgba(255,171,0,0.15); color: #ffab00; }
        .badge-fail { background: rgba(255,23,68,0.15); color: #ff1744; }
        .checks-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                       gap: 0.75rem; }
        .check-item { background: #0f3460; border-radius: 6px; padding: 0.75rem 1rem; }
        .check-name { font-size: 0.85rem; font-weight: 500; margin-bottom: 0.25rem; }
        .check-detail { font-size: 0.75rem; color: #aaa; }
        .timecodes { font-size: 0.7rem; color: #888; margin-top: 0.25rem; font-family: monospace; }
        .corrections { margin-top: 0.5rem; font-size: 0.8rem; }
        .corrections span { background: rgba(0,150,255,0.15); color: #4fc3f7;
                            padding: 0.15rem 0.5rem; border-radius: 3px; margin-right: 0.5rem;
                            font-size: 0.7rem; }
        footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #333;
                 font-size: 0.75rem; color: #666; }
    </style>
</head>
<body>
    <h1>QC Report</h1>
    <div class="subtitle">
        Batch: {{ batch_id }} &nbsp;|&nbsp;
        Project: {{ project }} &nbsp;|&nbsp;
        Generated: {{ timestamp }} &nbsp;|&nbsp;
        Pipeline v{{ version }}
    </div>

    <div class="summary-bar">
        <div class="stat-card">
            <div class="label">Total Clips</div>
            <div class="value">{{ clips | length }}</div>
        </div>
        <div class="stat-card">
            <div class="label">Passed</div>
            <div class="value pass">{{ pass_count }}</div>
        </div>
        <div class="stat-card">
            <div class="label">Warnings</div>
            <div class="value warn">{{ warn_count }}</div>
        </div>
        <div class="stat-card">
            <div class="label">Failed</div>
            <div class="value fail">{{ fail_count }}</div>
        </div>
    </div>

    {% for clip in clips %}
    <div class="clip-card status-{{ clip.overall_status }}">
        <div class="clip-header">
            <div>
                <span class="clip-name">{{ clip.filename }}</span>
                <span class="clip-meta">
                    &nbsp; {{ clip.resolution }} &nbsp;|&nbsp;
                    {{ clip.frame_rate }}fps &nbsp;|&nbsp;
                    {{ "%.1f"|format(clip.duration) }}s &nbsp;|&nbsp;
                    {{ clip.codec }}
                </span>
            </div>
            <span class="badge badge-{{ clip.overall_status | lower }}">
                {{ clip.overall_status }}
            </span>
        </div>
        <div class="checks-grid">
            {% for check_name, check in clip.checks.items() %}
            <div class="check-item">
                <div class="check-name">
                    <span class="badge badge-{{ check.status | lower }}">{{ check.status }}</span>
                    &nbsp; {{ check_name | replace('_', ' ') | title }}
                </div>
                {% if check.get('illegal_frame_count') is not none %}
                <div class="check-detail">Illegal frames: {{ check.illegal_frame_count }}</div>
                {% endif %}
                {% if check.get('integrated_lufs') is not none %}
                <div class="check-detail">
                    Loudness: {{ check.integrated_lufs }} LUFS
                    (target: {{ check.target_lufs }})
                </div>
                {% endif %}
                {% if check.get('count') is not none and check.count > 0 %}
                <div class="check-detail">Detections: {{ check.count }}</div>
                {% endif %}
                {% if check.get('interlaced_total') is not none and check.interlaced_total > 0 %}
                <div class="check-detail">Interlaced frames: {{ check.interlaced_total }}</div>
                {% endif %}
                {% if check.timecodes %}
                <div class="timecodes">{{ check.timecodes[:5] | join(', ') }}
                    {% if check.timecodes | length > 5 %}... +{{ check.timecodes | length - 5 }} more{% endif %}
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% if clip.correctable %}
        <div class="corrections">
            Auto-correctable:
            {% for c in clip.correctable %}<span>{{ c | replace('_', ' ') }}</span>{% endfor %}
        </div>
        {% endif %}
        {% if clip.requires_manual_review %}
        <div class="corrections">
            Manual review:
            {% for c in clip.requires_manual_review %}<span>{{ c | replace('_', ' ') }}</span>{% endfor %}
        </div>
        {% endif %}
    </div>
    {% endfor %}

    <footer>
        AI Video QC Pipeline &mdash; Trope Media Ltd &mdash; Report generated {{ timestamp }}
    </footer>
</body>
</html>"""


def generate_json_report(
    clips: list[dict],
    batch_id: str,
    project: str,
    output_path: Path,
    corrections: Optional[list[dict]] = None,
) -> Path:
    """Generate a JSON report for a batch of clips."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_data = {
        "batch_id": batch_id,
        "project": project,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "1.0.0",
        "total_clips": len(clips),
        "pass_count": sum(1 for c in clips if c.get("overall_status") == "PASS"),
        "warn_count": sum(1 for c in clips if c.get("overall_status") == "WARN"),
        "fail_count": sum(1 for c in clips if c.get("overall_status") == "FAIL"),
        "clips": clips,
    }

    if corrections:
        report_data["corrections"] = corrections

    with open(output_path, "w") as f:
        json.dump(report_data, f, indent=2, default=str)

    logger.info("JSON report written: %s", output_path)
    return output_path


def generate_html_report(
    clips: list[dict],
    batch_id: str,
    project: str,
    output_path: Path,
) -> Path:
    """Generate an HTML report for a batch of clips."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = Environment(autoescape=select_autoescape(["html"]))
    # Allow .get() in templates
    env.globals["hasattr"] = hasattr
    template = env.from_string(HTML_TEMPLATE)

    pass_count = sum(1 for c in clips if c.get("overall_status") == "PASS")
    warn_count = sum(1 for c in clips if c.get("overall_status") == "WARN")
    fail_count = sum(1 for c in clips if c.get("overall_status") == "FAIL")

    html = template.render(
        batch_id=batch_id,
        project=project,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        version="1.0.0",
        clips=clips,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
    )

    with open(output_path, "w") as f:
        f.write(html)

    logger.info("HTML report written: %s", output_path)
    return output_path
