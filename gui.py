#!/usr/bin/env python3
"""AI Video QC Pipeline — Lightweight GUI for testers.

Launch with:
    python gui.py
"""

import logging
import queue
import shutil
import subprocess
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pipeline_orchestrator import run_pipeline
from src.config import (
    ensure_directories,
    load_pipeline_config,
    load_qc_thresholds,
)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class QueueHandler(logging.Handler):
    """Logging handler that pushes formatted records into a queue."""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


class PipelineGUI:
    """Main GUI application."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AI Video QC Pipeline")
        self.root.geometry("780x620")
        self.root.minsize(640, 480)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.running = False
        self.last_html_report: Path | None = None
        self.last_json_report: Path | None = None

        self._build_ui()
        self._poll_log_queue()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # --- Project folder (where all outputs go) ---
        frame_project = ttk.LabelFrame(self.root, text="Project Folder (reports, thumbnails, corrected clips)")
        frame_project.pack(fill="x", **pad)

        self.project_var = tk.StringVar()
        self.project_entry = ttk.Entry(frame_project, textvariable=self.project_var, state="readonly")
        self.project_entry.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=6)
        self.project_btn = ttk.Button(frame_project, text="Browse\u2026", command=self._browse_project)
        self.project_btn.pack(side="right", padx=(4, 8), pady=6)

        # --- Batch directory ---
        frame_batch = ttk.LabelFrame(self.root, text="Batch Directory (input video files)")
        frame_batch.pack(fill="x", **pad)

        self.batch_var = tk.StringVar()
        self.batch_entry = ttk.Entry(frame_batch, textvariable=self.batch_var, state="readonly")
        self.batch_entry.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=6)
        self.browse_btn = ttk.Button(frame_batch, text="Browse\u2026", command=self._browse_batch)
        self.browse_btn.pack(side="right", padx=(4, 8), pady=6)

        # --- Options ---
        frame_opts = ttk.LabelFrame(self.root, text="Options")
        frame_opts.pack(fill="x", **pad)

        # Tier
        ttk.Label(frame_opts, text="Tier:").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.tier_var = tk.StringVar(value="standard")
        self.tier_combo = ttk.Combobox(
            frame_opts, textvariable=self.tier_var,
            values=["standard", "premium", "both"], state="readonly", width=12,
        )
        self.tier_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        # QC Only
        self.qc_only_var = tk.BooleanVar(value=False)
        self.qc_only_var.trace_add("write", self._on_qc_only_changed)
        self.qc_only_chk = ttk.Checkbutton(frame_opts, text="QC Only", variable=self.qc_only_var)
        self.qc_only_chk.grid(row=0, column=2, padx=16, pady=4)

        # Auto-correct
        self.auto_correct_var = tk.BooleanVar(value=True)
        self.auto_correct_chk = ttk.Checkbutton(
            frame_opts, text="Auto-Correct", variable=self.auto_correct_var,
        )
        self.auto_correct_chk.grid(row=0, column=3, padx=16, pady=4)

        frame_opts.columnconfigure(4, weight=1)

        # --- Config files ---
        frame_cfg = ttk.LabelFrame(self.root, text="Configuration")
        frame_cfg.pack(fill="x", **pad)

        ttk.Label(frame_cfg, text="Pipeline config:").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.config_var = tk.StringVar(value="config/pipeline_config.yaml")
        self.config_entry = ttk.Entry(frame_cfg, textvariable=self.config_var)
        self.config_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self.config_btn = ttk.Button(frame_cfg, text="Browse\u2026", command=lambda: self._browse_file(self.config_var))
        self.config_btn.grid(row=0, column=2, padx=(4, 8), pady=4)

        ttk.Label(frame_cfg, text="QC thresholds:").grid(row=1, column=0, sticky="w", padx=(8, 4), pady=4)
        self.thresh_var = tk.StringVar(value="config/qc_thresholds.yaml")
        self.thresh_entry = ttk.Entry(frame_cfg, textvariable=self.thresh_var)
        self.thresh_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.thresh_btn = ttk.Button(frame_cfg, text="Browse\u2026", command=lambda: self._browse_file(self.thresh_var))
        self.thresh_btn.grid(row=1, column=2, padx=(4, 8), pady=4)

        frame_cfg.columnconfigure(1, weight=1)

        # --- Action bar ---
        frame_action = ttk.Frame(self.root)
        frame_action.pack(fill="x", **pad)

        self.run_btn = ttk.Button(frame_action, text="Run Pipeline", command=self._start_pipeline)
        self.run_btn.pack(side="left", padx=4)

        self.clear_btn = ttk.Button(frame_action, text="Clear Log", command=self._clear_log)
        self.clear_btn.pack(side="left", padx=4)

        self.open_report_btn = ttk.Button(
            frame_action, text="Open Report", command=self._open_html_report, state="disabled",
        )
        self.open_report_btn.pack(side="left", padx=4)

        self.open_folder_btn = ttk.Button(
            frame_action, text="Open Project Folder", command=self._open_project_folder, state="disabled",
        )
        self.open_folder_btn.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Idle")
        self.status_label = ttk.Label(frame_action, textvariable=self.status_var, foreground="gray")
        self.status_label.pack(side="right", padx=8)

        # --- Log output ---
        frame_log = ttk.LabelFrame(self.root, text="Output")
        frame_log.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(
            frame_log, wrap="word", state="disabled",
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
            font=("Menlo", 11),
        )
        scrollbar = ttk.Scrollbar(frame_log, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

    # ------------------------------------------------------------ Actions

    def _on_qc_only_changed(self, *_args: object) -> None:
        """Disable Auto-Correct and Tier when QC Only is checked."""
        if self.qc_only_var.get():
            self.auto_correct_var.set(False)
            self.auto_correct_chk.configure(state="disabled")
            self.tier_combo.configure(state="disabled")
        else:
            self.auto_correct_chk.configure(state="normal")
            self.tier_combo.configure(state="readonly")

    def _browse_project(self) -> None:
        path = filedialog.askdirectory(title="Select project folder for outputs")
        if path:
            self.project_var.set(path)

    def _browse_batch(self) -> None:
        path = filedialog.askdirectory(title="Select batch directory")
        if path:
            self.batch_var.set(path)

    def _browse_file(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select YAML config",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            var.set(path)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_html_report(self) -> None:
        if self.last_html_report and self.last_html_report.exists():
            webbrowser.open(self.last_html_report.as_uri())

    def _open_project_folder(self) -> None:
        folder = self.project_var.get().strip()
        if folder and Path(folder).is_dir():
            subprocess.Popen(["open", folder])

    def _start_pipeline(self) -> None:
        project_folder = self.project_var.get().strip()
        if not project_folder:
            messagebox.showerror("Error", "Please select a project folder for outputs.")
            return

        batch = self.batch_var.get().strip()
        if not batch:
            messagebox.showerror("Error", "Please select a batch directory.")
            return
        if not Path(batch).is_dir():
            messagebox.showerror("Error", f"Directory not found:\n{batch}")
            return

        self._set_controls(False)
        self._set_status("Running\u2026", "#2196F3")
        self.open_report_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")
        self.last_html_report = None
        self.last_json_report = None

        thread = threading.Thread(target=self._run_pipeline_thread, daemon=True)
        thread.start()

    def _run_pipeline_thread(self) -> None:
        """Worker thread — runs pipeline and posts results via queue."""
        # Set up logging: file handler + queue handler for GUI
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        # Remove any pre-existing handlers to avoid duplicates
        root_logger.handlers.clear()

        formatter = logging.Formatter(LOG_FORMAT)

        # File handler
        config_path = self.config_var.get()
        try:
            config = load_pipeline_config(config_path)
        except Exception as exc:
            self.log_queue.put(f"ERROR: Failed to load config: {exc}")
            self.root.event_generate("<<PipelineError>>")
            return

        # Override pipeline_root with user-chosen project folder
        project_folder = Path(self.project_var.get())
        config.pipeline_root = project_folder

        log_dir = config.pipeline_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fh = logging.FileHandler(log_dir / f"pipeline_{timestamp}.log")
        fh.setFormatter(formatter)
        root_logger.addHandler(fh)

        # Queue handler (feeds the GUI text widget)
        qh = QueueHandler(self.log_queue)
        qh.setFormatter(formatter)
        root_logger.addHandler(qh)

        try:
            thresholds = load_qc_thresholds(self.thresh_var.get())
            ensure_directories(config.pipeline_root)

            # Clean old thumbnails for this batch to prevent bloat
            batch_dir = Path(self.batch_var.get())
            batch_id = batch_dir.name
            thumbs_dir = config.pipeline_root / "reports" / "thumbs" / batch_id
            if thumbs_dir.exists():
                shutil.rmtree(thumbs_dir)
                logging.getLogger("pipeline").info(
                    "Cleared previous thumbnails: %s", thumbs_dir,
                )

            summary = run_pipeline(
                batch_dir=batch_dir,
                config=config,
                thresholds=thresholds,
                qc_only=self.qc_only_var.get(),
                auto_correct=self.auto_correct_var.get(),
                tier=self.tier_var.get(),
            )

            # Store report paths for the "Open Report" buttons
            if summary.get("qc_report_html"):
                self.last_html_report = Path(summary["qc_report_html"])
            if summary.get("qc_report_json"):
                self.last_json_report = Path(summary["qc_report_json"])

            self.root.event_generate("<<PipelineDone>>")

        except SystemExit:
            logging.getLogger("pipeline").error("Pipeline exited unexpectedly.")
            self.root.event_generate("<<PipelineError>>")

        except Exception as exc:
            logging.getLogger("pipeline").error("Pipeline failed: %s", exc, exc_info=True)
            self.root.event_generate("<<PipelineError>>")

        finally:
            root_logger.removeHandler(fh)
            root_logger.removeHandler(qh)
            fh.close()

    # --------------------------------------------------------- Log polling

    def _poll_log_queue(self) -> None:
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        self.root.after(100, self._poll_log_queue)

    # ------------------------------------------------------------ Helpers

    def _set_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        readonly_state = "readonly" if enabled else "disabled"
        self.project_btn.configure(state=state)
        self.browse_btn.configure(state=state)
        self.run_btn.configure(state=state)
        self.qc_only_chk.configure(state=state)
        self.config_entry.configure(state=state)
        self.config_btn.configure(state=state)
        self.thresh_entry.configure(state=state)
        self.thresh_btn.configure(state=state)
        self.running = not enabled

        # Respect QC Only state when re-enabling
        if enabled and self.qc_only_var.get():
            self.auto_correct_chk.configure(state="disabled")
            self.tier_combo.configure(state="disabled")
        else:
            self.auto_correct_chk.configure(state=state)
            self.tier_combo.configure(state=readonly_state)

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(foreground=color)

    def _on_pipeline_done(self, _event: tk.Event) -> None:
        self._set_controls(True)
        self._set_status("Complete", "#4CAF50")
        # Enable report buttons if reports were generated
        if self.last_html_report and self.last_html_report.exists():
            self.open_report_btn.configure(state="normal")
        if self.project_var.get().strip():
            self.open_folder_btn.configure(state="normal")

    def _on_pipeline_error(self, _event: tk.Event) -> None:
        self._set_controls(True)
        self._set_status("Error", "#F44336")
        # Still enable folder button so user can inspect partial output
        if self.project_var.get().strip():
            self.open_folder_btn.configure(state="normal")


def main() -> None:
    root = tk.Tk()
    app = PipelineGUI(root)

    # Bind custom events for thread -> main thread signaling
    root.bind("<<PipelineDone>>", app._on_pipeline_done)
    root.bind("<<PipelineError>>", app._on_pipeline_error)

    root.mainloop()


if __name__ == "__main__":
    main()
