#!/usr/bin/env python3
"""PE RRF Batch Tool - a single Windows GUI wrapping this project's own real batch
pipeline (RRF import via io_import_rrf.py's real apply_real_world_scale/
snap_to_ground options + an optional Smart UV Project + faction-paint pass), built
2026-08-07 after running this exact pipeline by hand via ad-hoc scratchpad scripts +
bash drivers for the Cogs of War project's CustomB folder (154/154 vehicles, real
scale + ground-snap fix, real faction classification) - built so the NEXT folder
(e.g. a Desert theatre set) doesn't need that same manual, multi-hour, script-by-
script process repeated by hand.

Runs each file as its OWN fresh Blender subprocess with a hard per-file timeout, not
one long-running loop - real, load-bearing design choice, not just caution: a real
Panzer Elite RRF (Psw232.RRF) was found to hang bmesh.ops.recalc_face_normals()
indefinitely on degenerate geometry in one part during this project's own real batch
work, costing over 4 hours before it was diagnosed. That specific bug is now fixed
in io_import_rrf.py itself, but per-file isolation stays the right call for any
future unknown edge case - it's what let today's real batch recover cleanly (skip
the one bad file, keep going) instead of losing the whole run.

No external dependencies - tkinter + subprocess + threading + json are all stdlib.
Double-click to run (a .pyw file opens with pythonw.exe, no console window).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

TOOLS_DIR = Path(__file__).resolve().parent
WORKER_SCRIPT = TOOLS_DIR / "_worker_convert.py"
FACTION_MAP_PATH = TOOLS_DIR / "faction_map.json"
CONFIG_PATH = TOOLS_DIR / "batch_gui_config.json"

FACTIONS = ["soviet", "axis", "western", "skip"]

DEFAULT_CONFIG = {
    "blender_exe": r"G:\SteamLibrary\steamapps\common\Blender\blender.exe",
    "src_dir": "",
    "out_dir": "",
    "basecolor_png": r"K:\Cogs of War\models\Kv176_BaseColor_WW2.png",
    "apply_scale": True,
    "snap_ground": True,
    "do_uv_paint": True,
    "timeout_s": 45,
}


def load_json(path, default):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class BatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PE RRF Batch Tool")
        self.geometry("880x640")
        self.minsize(760, 560)

        self.config_data = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
        self.faction_map = load_json(FACTION_MAP_PATH, {})

        self.worker_thread = None
        self.cancel_flag = threading.Event()
        self.log_queue = queue.Queue()

        self._build_ui()
        self.after(100, self._poll_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------- UI layout

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        folders = ttk.LabelFrame(self, text="Folders")
        folders.pack(fill="x", **pad)

        self.src_var = tk.StringVar(value=self.config_data["src_dir"])
        self.out_var = tk.StringVar(value=self.config_data["out_dir"])
        self.blender_var = tk.StringVar(value=self.config_data["blender_exe"])
        self.basecolor_var = tk.StringVar(value=self.config_data["basecolor_png"])

        self._folder_row(folders, "Source folder (.RRF files):", self.src_var, self._browse_src)
        self._folder_row(folders, "Output folder (.blend files):", self.out_var, self._browse_out)
        self._folder_row(folders, "Blender executable:", self.blender_var, self._browse_blender, is_file=True)
        self._folder_row(folders, "Shared BaseColor texture (.png):", self.basecolor_var, self._browse_basecolor, is_file=True)

        options = ttk.LabelFrame(self, text="Options")
        options.pack(fill="x", **pad)

        self.apply_scale_var = tk.BooleanVar(value=self.config_data["apply_scale"])
        self.snap_ground_var = tk.BooleanVar(value=self.config_data["snap_ground"])
        self.do_uv_paint_var = tk.BooleanVar(value=self.config_data["do_uv_paint"])
        self.timeout_var = tk.IntVar(value=self.config_data["timeout_s"])

        row = ttk.Frame(options)
        row.pack(fill="x", padx=4, pady=2)
        ttk.Checkbutton(row, text="Apply real-world scale (0.14)", variable=self.apply_scale_var).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Snap to ground (Z=0)", variable=self.snap_ground_var).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Smart UV + faction paint", variable=self.do_uv_paint_var).pack(side="left", padx=6)
        ttk.Label(row, text="Per-file timeout (s):").pack(side="left", padx=(20, 4))
        ttk.Spinbox(row, from_=10, to=600, textvariable=self.timeout_var, width=6).pack(side="left")

        scan_row = ttk.Frame(self)
        scan_row.pack(fill="x", **pad)
        ttk.Button(scan_row, text="Scan Source Folder", command=self._scan_folder).pack(side="left")
        self.scan_summary_var = tk.StringVar(value="Not scanned yet.")
        ttk.Label(scan_row, textvariable=self.scan_summary_var).pack(side="left", padx=10)
        self.edit_unclassified_btn = ttk.Button(scan_row, text="Edit Unclassified...", command=self._edit_unclassified, state="disabled")
        self.edit_unclassified_btn.pack(side="left", padx=10)

        run_row = ttk.Frame(self)
        run_row.pack(fill="x", **pad)
        self.run_btn = ttk.Button(run_row, text="Run Batch", command=self._start_run)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(run_row, text="Cancel", command=self._cancel_run, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.open_output_btn = ttk.Button(run_row, text="Open Output Folder", command=self._open_output)
        self.open_output_btn.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", **pad)

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, wrap="none", state="disabled", height=20)
        vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=8, pady=(0, 6))

        self._scanned_files = []  # basenames (no extension), lowercase

    def _folder_row(self, parent, label, var, browse_cmd, is_file=False):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        ttk.Label(row, text=label, width=32, anchor="w").pack(side="left")
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse...", command=browse_cmd).pack(side="left")

    def _browse_src(self):
        d = filedialog.askdirectory(title="Select folder containing .RRF files", initialdir=self.src_var.get() or None)
        if d:
            self.src_var.set(d)

    def _browse_out(self):
        d = filedialog.askdirectory(title="Select output folder for .blend files", initialdir=self.out_var.get() or None)
        if d:
            self.out_var.set(d)

    def _browse_blender(self):
        f = filedialog.askopenfilename(title="Select blender.exe", filetypes=[("Blender executable", "blender.exe"), ("All files", "*.*")])
        if f:
            self.blender_var.set(f)

    def _browse_basecolor(self):
        f = filedialog.askopenfilename(title="Select shared BaseColor .png", filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if f:
            self.basecolor_var.set(f)

    # ---------------------------------------------------------- scan / classify

    def _scan_folder(self):
        src = self.src_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("PE RRF Batch Tool", "Pick a valid source folder first.")
            return
        files = [f for f in os.listdir(src) if f.lower().endswith(".rrf")]
        self._scanned_files = sorted(os.path.splitext(f)[0].lower() for f in files)
        classified = sum(1 for b in self._scanned_files if b in self.faction_map)
        unclassified = len(self._scanned_files) - classified
        self.scan_summary_var.set(f"{len(self._scanned_files)} .rrf file(s) found - {classified} classified, {unclassified} unclassified.")
        self.edit_unclassified_btn.config(state="normal" if unclassified else "disabled")
        self._log(f"Scanned '{src}': {len(self._scanned_files)} file(s), {unclassified} need classification.")

    def _edit_unclassified(self):
        unclassified = [b for b in self._scanned_files if b not in self.faction_map]
        if not unclassified:
            messagebox.showinfo("PE RRF Batch Tool", "Nothing unclassified.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Classify New Vehicles")
        dialog.geometry("480x520")
        ttk.Label(dialog, text="Pick a faction for each new file (defaults to 'skip' - "
                                "safe, just leaves it at its own natural UV center instead "
                                "of a specific faction color).", wraplength=440, justify="left").pack(padx=10, pady=8, anchor="w")

        canvas = tk.Canvas(dialog)
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=6)
        scroll.pack(side="right", fill="y", pady=6)

        vars_by_name = {}
        for name in unclassified:
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=name, width=30, anchor="w").pack(side="left")
            v = tk.StringVar(value="skip")
            ttk.Combobox(row, textvariable=v, values=FACTIONS, state="readonly", width=12).pack(side="left")
            vars_by_name[name] = v

        def save_and_close():
            for name, v in vars_by_name.items():
                self.faction_map[name] = v.get()
            save_json(FACTION_MAP_PATH, self.faction_map)
            self._log(f"Saved {len(vars_by_name)} new classification(s) to faction_map.json.")
            dialog.destroy()
            self._scan_folder()

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill="x", pady=8)
        ttk.Button(btn_row, text="Save & Close", command=save_and_close).pack(side="right", padx=10)

    # ---------------------------------------------------------- run batch

    def _save_config(self):
        self.config_data.update({
            "blender_exe": self.blender_var.get(),
            "src_dir": self.src_var.get(),
            "out_dir": self.out_var.get(),
            "basecolor_png": self.basecolor_var.get(),
            "apply_scale": self.apply_scale_var.get(),
            "snap_ground": self.snap_ground_var.get(),
            "do_uv_paint": self.do_uv_paint_var.get(),
            "timeout_s": self.timeout_var.get(),
        })
        save_json(CONFIG_PATH, self.config_data)

    def _start_run(self):
        src = self.src_var.get().strip()
        out = self.out_var.get().strip()
        blender = self.blender_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("PE RRF Batch Tool", "Pick a valid source folder first.")
            return
        if not out:
            messagebox.showerror("PE RRF Batch Tool", "Pick an output folder first.")
            return
        if not os.path.isfile(blender):
            messagebox.showerror("PE RRF Batch Tool", "blender.exe path is not valid.")
            return
        os.makedirs(out, exist_ok=True)
        self._save_config()

        files = sorted(f for f in os.listdir(src) if f.lower().endswith(".rrf"))
        if not files:
            messagebox.showinfo("PE RRF Batch Tool", "No .rrf files found in the source folder.")
            return

        self.cancel_flag.clear()
        self.run_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress.config(maximum=len(files), value=0)
        self._log(f"\n=== Starting batch: {len(files)} file(s) ===")

        self.worker_thread = threading.Thread(
            target=self._run_batch_worker,
            args=(files, src, out, blender),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_batch_worker(self, files, src, out, blender):
        ok_count = 0
        fail_count = 0
        timeout_s = self.timeout_var.get()
        apply_scale = "1" if self.apply_scale_var.get() else "0"
        snap_ground = "1" if self.snap_ground_var.get() else "0"
        do_uv_paint = "1" if self.do_uv_paint_var.get() else "0"
        basecolor_png = self.basecolor_var.get()
        log_path = os.path.join(out, "_batch_gui_log.txt")
        results = []

        for i, fname in enumerate(files, 1):
            if self.cancel_flag.is_set():
                self.log_queue.put(("log", f"[{i}/{len(files)}] CANCELLED - stopping."))
                break

            base = os.path.splitext(fname)[0].lower()
            faction = self.faction_map.get(base, "skip") if do_uv_paint == "1" else "none"
            src_path = os.path.join(src, fname)
            out_path = os.path.join(out, os.path.splitext(fname)[0] + ".blend")

            cmd = [
                blender, "--background", "--python", str(WORKER_SCRIPT), "--",
                src_path, out_path, apply_scale, snap_ground, do_uv_paint, faction, basecolor_png,
            ]
            t0 = time.time()
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
                elapsed = time.time() - t0
                if proc.returncode == 0 and "RESULT_OK" in proc.stdout:
                    detail = [l for l in proc.stdout.splitlines() if "RESULT_OK" in l][-1]
                    self.log_queue.put(("log", f"[{i}/{len(files)}] OK   {fname} - {detail} ({elapsed:.1f}s)"))
                    results.append((fname, "OK", detail))
                    ok_count += 1
                else:
                    err_lines = [l for l in (proc.stdout + proc.stderr).splitlines() if "error" in l.lower() or "exception" in l.lower()]
                    err = err_lines[-1] if err_lines else f"exit={proc.returncode}"
                    self.log_queue.put(("log", f"[{i}/{len(files)}] FAIL {fname} - {err}"))
                    results.append((fname, "FAIL", err))
                    fail_count += 1
            except subprocess.TimeoutExpired:
                self.log_queue.put(("log", f"[{i}/{len(files)}] TIMEOUT {fname} - exceeded {timeout_s}s, killed"))
                results.append((fname, "TIMEOUT", f"exceeded {timeout_s}s"))
                fail_count += 1
            except Exception as e:
                self.log_queue.put(("log", f"[{i}/{len(files)}] FAIL {fname} - {type(e).__name__}: {e}"))
                results.append((fname, "FAIL", str(e)))
                fail_count += 1

            self.log_queue.put(("progress", i))

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"PE RRF Batch Tool log - {ok_count} OK, {fail_count} failed, of {len(files)}\n\n")
            for fname, status, detail in results:
                f.write(f"  [{status}] {fname}: {detail}\n")

        summary = f"=== DONE: {ok_count} OK, {fail_count} failed/timed-out, of {len(files)} ==="
        self.log_queue.put(("log", summary))
        self.log_queue.put(("log", f"Log written to: {log_path}"))
        self.log_queue.put(("done", (ok_count, fail_count, len(files))))

    def _cancel_run(self):
        self.cancel_flag.set()
        self.cancel_btn.config(state="disabled")
        self.status_var.set("Cancelling after the current file finishes...")

    # ---------------------------------------------------------- log/queue plumbing

    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "progress":
                    self.progress.config(value=payload)
                elif kind == "done":
                    ok, fail, total = payload
                    self.run_btn.config(state="normal")
                    self.cancel_btn.config(state="disabled")
                    self.status_var.set(f"Done: {ok}/{total} OK, {fail} failed/timed-out.")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _open_output(self):
        out = self.out_var.get().strip()
        if out and os.path.isdir(out):
            os.startfile(out)
        else:
            messagebox.showerror("PE RRF Batch Tool", "Output folder isn't set or doesn't exist yet.")

    def _on_close(self):
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    app = BatchApp()
    app.mainloop()
