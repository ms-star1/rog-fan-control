"""Startet die GUI aus dem Projektordner und rendert einige Ansichten als PNG.

Aufruf: XDG_CONFIG_HOME=test/cfg python3 test/shot.py [ansicht ...]
Ansichten: overview, overview_tall, wide, narrow, curves, limits, setup, password
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
views = sys.argv[1:] or ["overview", "curves", "limits"]
sys.argv = ["rog-fan-control"]
mod = runpy.run_path(REPO + "/rog_fan_gui.py", run_name="gui")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

app = mod["App"]()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
state = {"dialog": None}


def snap(win, name):
    p = Gtk.WidgetPaintable(widget=win)
    s = Gtk.Snapshot()
    p.snapshot(s, win.get_width(), win.get_height())
    win.get_renderer().render_texture(s.to_node(), None).save_to_png(f"{HERE}/{name}.png")


def prepare(win, view):
    if state["dialog"]:
        state["dialog"].force_close()
        state["dialog"] = None
    sizes = {"overview_tall": (780, 1500), "wide": (1440, 760), "narrow": (560, 900)}
    win.set_default_size(*sizes.get(view, (900, 900)))
    if view in ("wide", "narrow"):
        win.stack.set_visible_child_name("overview")
    if view in ("overview", "overview_tall", "curves", "limits"):
        win.stack.set_visible_child_name(view.replace("_tall", ""))
    elif view == "setup":
        d = mod["SetupDialog"](win, False)
        d.present(win)
        state["dialog"] = d
    elif view == "password":
        win._password_info()


def step(i=0):
    win = app.props.active_window
    if i > 0:
        snap(win, views[i - 1])
    if i < len(views):
        prepare(win, views[i])
        GLib.timeout_add(2000, step, i + 1)
    else:
        app.quit()
    return False


app.connect("activate", lambda *_: GLib.timeout_add(int(os.environ.get("SHOT_DELAY", "2000")), step, 0))
app.run([])
