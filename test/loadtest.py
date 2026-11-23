"""Startet die GUI, schaltet den Lasttest GPU -> CPU um und schließt das Fenster (Funktionstest)."""
import os, runpy, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.argv = ["x"]
mod = runpy.run_path(REPO + "/rog_fan_gui.py", run_name="gui")
from gi.repository import Gio, GLib
app = mod["App"](); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
def q(): return subprocess.run(["nvidia-smi","--query-gpu=utilization.gpu,power.draw","--format=csv,noheader"],capture_output=True,text=True).stdout.strip()
def go():
    w = app.props.active_window
    w.load_toggle.set_active_name("gpu")
    GLib.timeout_add(7000, check, w); return False
def check(w):
    print("GPU-Last aktiv:", q(), "| Status:", w.load_status.get_subtitle(), flush=True)
    w.load_toggle.set_active_name("cpu")
    GLib.timeout_add(4000, close, w); return False
def close(w):
    print("Wechsel auf CPU:", w.load_status.get_subtitle(), "| GPU:", q(), flush=True)
    w.close(); return False
app.connect("activate", lambda *_: GLib.timeout_add(1500, go))
app.run([])
