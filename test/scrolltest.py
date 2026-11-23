"""Funktionstest der Diagrammleiste: schmales Fenster, mit den Pfeiltasten blättern."""
import os, runpy, sys
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.argv = ["x"]
mod = runpy.run_path(REPO + "/rog_fan_gui.py", run_name="gui")
from gi.repository import Gio, GLib
app = mod["App"](); app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
def state(w, label):
    a = w.chart_scroll.get_hadjustment()
    print(f"{label:16} Position {a.get_value():6.0f} von {a.get_upper() - a.get_page_size():4.0f} | "
          f"links {'an ' if w.chart_prev.get_sensitive() else 'aus'} rechts {'an ' if w.chart_next.get_sensitive() else 'aus'} "
          f"| Tasten sichtbar: {w.chart_next.get_visible()}", flush=True)
seq = []
def run(i=0):
    w = app.props.active_window
    if i == 0:
        w.set_default_size(560, 800)
    elif i == 1:
        state(w, "Start")
    elif i < 6:
        w.chart_next.emit("clicked"); GLib.timeout_add(400, lambda: state(w, f"rechts ×{i-1}") or False)
    elif i < 9:
        w.chart_prev.emit("clicked"); GLib.timeout_add(400, lambda: state(w, f"links ×{i-5}") or False)
    elif i == 9:
        w.set_default_size(1400, 800)
    elif i == 10:
        state(w, "breites Fenster")
    else:
        app.quit(); return False
    GLib.timeout_add(900, run, i + 1); return False
app.connect("activate", lambda *_: GLib.timeout_add(1500, run))
app.run([])
