# AI Video QC Pipeline

> Automated quality control, correction, and log conversion pipeline for AI-generated video.

## What It Does

AI-generated video (from tools like Wan 2.2, Kling, Runway, Sora, etc.) arrives in compressed 8-bit sRGB format with no dynamic range headroom, no broadcast-compliant metadata, and frequent technical defects. This pipeline bridges the gap between raw AI output and professional post-production workflows.

The pipeline performs three stages:

1. **QC Analysis** — Frame-by-frame technical analysis using FFmpeg, checking for broadcast legality, black frames, frozen frames, flash frames, interlacing, audio loudness compliance, silence, and letterboxing issues.
2. **Automated Correction** — Fixes correctable issues (colour clamping, loudness normalisation, deinterlacing) in a single FFmpeg pass with full audit logging.
3. **Log Conversion** — Applies a Rec.709-to-log LUT via FFmpeg, outputting 10-bit ProRes 422 HQ with correct colour metadata so the footage sits properly in a log-based grading pipeline.

Reports are generated in both HTML (visual dashboard) and JSON (machine-readable) formats.

## How It Works

The pipeline is controlled by a single orchestrator script. It scans a batch directory for video files, runs all QC analysis filters in a single FFmpeg pass per clip, parses the diagnostic output into structured data, optionally applies corrections, and then converts to a log colourspace via a .cube LUT. Each stage writes checkpoint files so interrupted runs can be resumed.

All FFmpeg analysis filters (signalstats, blackdetect, freezedetect, scdet, idet, ebur128, silencedetect, cropdetect) run in parallel on decoded frames in a single command, so each clip is only decoded once during analysis.

## Installation

### Requirements

- **Python 3.10+**
- **FFmpeg 6.0+** (must be available on PATH)
- **ffprobe** (ships with FFmpeg)

### Setup

```bash
# Clone the repo
git clone https://github.com/LaserDog80/ai-video-qc-pipeline.git
cd ai-video-qc-pipeline

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### LUT Setup

For log conversion, you need a Rec.709-to-log .cube LUT file. The recommended option is the "Generic Rec.709 to LOG" from the [IWLTBAP free LUT pack](https://www.iwltbap.com/). Place it at:

```
config/luts/rec709_to_log.cube
```

## Usage

### GUI (Recommended for Testers)

The GUI provides a point-and-click interface — no command line needed:

```bash
python gui.py
```

1. **Project Folder** — Choose a folder where all outputs will be saved (reports, thumbnails, corrected clips, logs). This keeps outputs separate from the source code and input files.
2. **Batch Directory** — Select the folder containing your input video files.
3. **Options** — Set tier, toggle QC Only or Auto-Correct as needed.
4. Click **Run Pipeline** and watch progress in the output pane.

Thumbnails from previous runs are automatically cleaned up when you re-run a batch, so the project folder won't accumulate stale files.

### Command Line

#### QC Analysis Only

```bash
python pipeline_orchestrator.py --batch input/project_name/batch_001/ --output /path/to/project_folder --qc-only
```

#### Standard Pipeline (QC + Correction + Log Conversion)

```bash
python pipeline_orchestrator.py --batch input/project_name/batch_001/ --output /path/to/project_folder --auto-correct --tier standard
```

#### Without Automatic Correction

```bash
python pipeline_orchestrator.py --batch input/project_name/batch_001/ --output /path/to/project_folder --no-auto-correct --tier standard
```

### Command-Line Options

| Flag | Description |
|------|-------------|
| `--batch PATH` | Path to the batch directory containing video clips (required) |
| `--output PATH` | Project folder for all outputs (reports, thumbnails, corrected clips, logs) |
| `--qc-only` | Run QC analysis only — no correction or conversion |
| `--tier standard\|premium\|both` | Output tier (default: `standard`) |
| `--auto-correct` | Enable automatic correction of fixable issues |
| `--no-auto-correct` | Disable automatic correction |
| `--config PATH` | Path to pipeline config file (default: `config/pipeline_config.yaml`) |
| `--thresholds PATH` | Path to QC thresholds file (default: `config/qc_thresholds.yaml`) |

### Output

All outputs are written to the **project folder** (set via `--output` on the CLI or "Project Folder" in the GUI). If not specified, outputs go to the current directory. After a run, you'll find:

- **Reports** in `reports/` — HTML dashboard and JSON data for each batch
- **Corrected clips** in `staging/corrected/` — ProRes 422 HQ with fixes applied
- **Log-converted clips** in `output/standard/` — 10-bit ProRes with log LUT applied
- **Thumbnails** in `reports/thumbs/` — extracted frames at flagged timecodes (auto-cleaned on re-run)
- **Logs** in `logs/` — timestamped pipeline logs

### Configuration

Edit `config/pipeline_config.yaml` to change pipeline behaviour (output codec, frame rate target, LUT paths, loudness standard, hardware acceleration, etc.).

Edit `config/qc_thresholds.yaml` to adjust pass/fail thresholds for each QC check.

## Project Structure

```
ai-video-qc-pipeline/
├── gui.py                      # GUI for testers (Tkinter)
├── pipeline_orchestrator.py    # CLI entry point
├── src/
│   ├── config.py               # Configuration loader
│   ├── qc_engine.py            # QC analysis (FFmpeg command builder + parser)
│   ├── correction_engine.py    # Automated correction (two-pass loudnorm, etc.)
│   ├── log_converter.py        # Standard Rec.709-to-log conversion
│   └── report_generator.py     # HTML and JSON report generation
├── tests/                      # pytest test suite
├── config/
│   ├── pipeline_config.yaml    # Master configuration
│   ├── qc_thresholds.yaml      # QC pass/fail thresholds
│   └── luts/                   # LUT files (.cube)
├── docs/                       # Technical plan and documentation
└── requirements.txt            # Python dependencies
```

## Running Tests

```bash
pytest
```

## Current Status

This is the first prototype implementing Phases 1–3 of the technical plan:

- **Phase 1 (QC Engine)** — Complete. All 8 analysis checks implemented with FFmpeg filter parsing.
- **Phase 2 (Correction Engine)** — Complete. Broadcast clamping, two-pass loudnorm, conditional deinterlacing.
- **Phase 3 (Standard Log Conversion)** — Complete. LUT application with correct colour metadata tagging.
- **Phase 4 (Premium/Resolve)** — Not yet implemented. Requires Topaz Video AI and DaVinci Resolve Studio.
- **Phase 5 (Scheduling)** — Not yet implemented.

## Development

See `CLAUDE.md` for development conventions and workflow.

## Licence

Proprietary — Trope Media Ltd.
