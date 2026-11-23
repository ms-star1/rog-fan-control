"""Startet die GUI, rendert jede Seite als PNG und beendet sich."""
import sys, runpy
sys.argv = ["rog-fan-control"]
HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
REPO = __import__("os").path.dirname(HERE)
sys.path.insert(0, REPO)
mod = runpy.run_path(REPO + "/rog_fan_gui.py", run_name="gui")
from gi.repository import GLib, Gtk
App = mod["App"]
out = HERE
app = App()
pages = ["overview", "curves", "limits"]
def shoot(i=0):
    win = app.props.active_window
    if i > 0:
        p = Gtk.WidgetPaintable(widget=win)
        snap = Gtk.Snapshot()
        p.snapshot(snap, win.get_width(), win.get_height())
        tex = win.get_renderer().render_texture(snap.to_node(), None)
        tex.save_to_png(f"{out}/{pages[i-1]}.png")
    if i < len(pages):
        win.stack.set_visible_child_name(pages[i])
        GLib.timeout_add(2500, shoot, i + 1)
    else:
        app.quit()
    return False
app.connect("activate", lambda *_: GLib.timeout_add(4000, shoot, 0))
app.run([])
