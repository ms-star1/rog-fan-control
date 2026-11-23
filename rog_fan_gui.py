#!/usr/bin/python3
"""ROG Fan Control – GTK4/libadwaita-Oberfläche für Lüfter, Profile und Limits."""
import json
import math
import os
import subprocess
import sys
import time
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gio, GLib, Gtk, PangoCairo  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rogfan_hw as hw  # noqa: E402

APP_ID = "org.local.RogFanControl"
HISTORY_SECONDS = 300
UI_SETTINGS = os.path.join(GLib.get_user_config_dir(), "rog-fan-control", "ui.json")
LOAD_TOOL = os.path.join(os.path.dirname(os.path.realpath(__file__)), "rog-fan-load")
LOAD_MODES = [("off", "Aus"), ("cpu", "CPU"), ("gpu", "GPU"), ("all", "Alles")]
PASSWORD_TITLE = "Warum fragt die App nach dem Passwort?"
PASSWORD_TEXT = (
    "Lüfterkurven, Vollgas und Leistungslimits werden über Kernel-Schnittstellen der "
    "ASUS-Firmware gesetzt. Diese darf aus Sicherheitsgründen nur der Administrator (root) "
    "ändern – sonst könnte jedes Programm die Kühlung abschalten.\n\n"
    "Die App selbst läuft ohne Sonderrechte. Nur wenn du etwas änderst (Anwenden, Vollgas, "
    "Einrichtung, Dienst aktivieren), startet sie über polkit einen kleinen Helfer. Er nimmt "
    "ausschließlich geprüfte Werte an und führt keine beliebigen Befehle aus. GNOME merkt "
    "sich das Passwort danach einige Minuten.\n\n"
    "Kein Passwort brauchen: Anzeige, Verlauf, Lasttest und der Profilwechsel.")

# Farben, die auf hellem und dunklem Hintergrund lesbar sind (GNOME-Palette)
COL = {
    "cpu_temp": (0.21, 0.52, 0.89),
    "gpu_temp": (0.90, 0.38, 0.00),
    "accent": (0.21, 0.52, 0.89),
    "factory": (0.55, 0.55, 0.55),
    "warn": (0.88, 0.11, 0.14),
}
FAN_COLORS = {"cpu": (0.21, 0.52, 0.89), "gpu": (0.90, 0.38, 0.00), "mid": (0.15, 0.64, 0.41)}
EXTRA_COLORS = [(0.57, 0.25, 0.67), (0.60, 0.40, 0.20), (0.85, 0.30, 0.55), (0.40, 0.45, 0.50)]

PRESETS = {
    "Leise": ([50, 60, 66, 72, 78, 84, 90, 95], [0, 8, 12, 18, 26, 36, 52, 75]),
    "Ausgewogen": ([45, 58, 65, 70, 76, 82, 88, 93], [5, 12, 20, 28, 38, 50, 68, 90]),
    "Kühl": ([40, 50, 58, 65, 72, 78, 84, 90], [20, 32, 45, 58, 70, 82, 95, 100]),
}


def hexcol(rgb):
    return "#%02x%02x%02x" % tuple(int(c * 255) for c in rgb)


def fan_color(key):
    if key in FAN_COLORS:
        return FAN_COLORS[key]
    extras = [k for k in hw.fan_keys() if k not in FAN_COLORS]
    return EXTRA_COLORS[extras.index(key) % len(EXTRA_COLORS)] if key in extras else EXTRA_COLORS[-1]


def preset_points(name, n):
    temps, pcts = PRESETS[name]
    return hw.resample_curve(temps, pcts, n)


def systemctl_active(units):
    try:
        r = subprocess.run(["systemctl", "is-active", *units],
                           capture_output=True, text=True, timeout=3)
        states = r.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        states = []
    return {u: (states[i] if i < len(states) else "unknown") for i, u in enumerate(units)}


def load_ui_settings():
    try:
        with open(UI_SETTINGS) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui_settings(data):
    try:
        os.makedirs(os.path.dirname(UI_SETTINGS), exist_ok=True)
        with open(UI_SETTINGS, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def padded(widget, v=8, h=10):
    widget.set_margin_top(v)
    widget.set_margin_bottom(v)
    widget.set_margin_start(h)
    widget.set_margin_end(h)
    return widget


# --------------------------------------------------------------------------- #
# Zeichenhilfen
# --------------------------------------------------------------------------- #

def fg_color(widget):
    c = widget.get_color()
    return c.red, c.green, c.blue


def draw_text(widget, cr, x, y, text, rgb, alpha=1.0, align="left"):
    layout = widget.create_pango_layout(None)
    layout.set_markup(f"<span size='small'>{GLib.markup_escape_text(text)}</span>", -1)
    w, h = layout.get_pixel_size()
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    cr.set_source_rgba(*rgb, alpha)
    cr.move_to(x, y - h / 2)
    PangoCairo.show_layout(cr, layout)


# --------------------------------------------------------------------------- #
# Live-Diagramm
# --------------------------------------------------------------------------- #

class LiveGraph(Gtk.DrawingArea):
    """Zwei übereinanderliegende Verläufe: Temperaturen und Drehzahlen.

    history: deque von (zeit, cpu_temp, gpu_temp, {lüfter: rpm})"""

    def __init__(self, history):
        super().__init__()
        self.history = history
        self.rpm_hint = 0  # gemessene Höchstdrehzahl (Einrichtung)
        self.set_content_height(260)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def _grid(self, cr, x0, y0, w, h, fg, vmin, vmax, step, unit):
        cr.set_line_width(1)
        v = vmin
        while v <= vmax + 1e-6:
            y = y0 + h - (v - vmin) / (vmax - vmin) * h
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(x0, y)
            cr.line_to(x0 + w, y)
            cr.stroke()
            label = f"{v / 1000:g}k" if unit == "RPM" and v else f"{int(v)}"
            draw_text(self, cr, x0 - 6, y, label, fg, 0.6, "right")
            v += step
        draw_text(self, cr, x0, y0 - 14, unit, fg, 0.6)

    def _line(self, cr, x0, y0, w, h, vmin, vmax, pts, rgb):
        if len(pts) < 2:
            return
        now = time.monotonic()
        cr.set_source_rgb(*rgb)
        cr.set_line_width(2)
        first = True
        for t, val in pts:
            x = x0 + w - (now - t) / HISTORY_SECONDS * w
            if x < x0:
                continue
            y = y0 + h - (min(max(val, vmin), vmax) - vmin) / (vmax - vmin) * h
            if first:
                cr.move_to(x, y)
                first = False
            else:
                cr.line_to(x, y)
        cr.stroke()

    def _draw(self, _area, cr, width, height):
        fg = fg_color(self)
        left, right, top, gap, bottom = 44, 8, 22, 44, 18
        ph = (height - top - gap - bottom) / 2
        pw = width - left - right

        self._grid(cr, left, top, pw, ph, fg, 20, 100, 20, "°C")
        for idx, key in ((1, "cpu_temp"), (2, "gpu_temp")):
            pts = [(e[0], e[idx]) for e in self.history if e[idx] is not None]
            self._line(cr, left, top, pw, ph, 20, 100, pts, COL[key])

        y0 = top + ph + gap
        rpms = [r for e in self.history for r in e[3].values()
                if r is not None and r <= hw.MAX_PLAUSIBLE_RPM]
        vmax = max(6000, self.rpm_hint, max(rpms, default=0))
        vmax = math.ceil(vmax / 2000) * 2000
        step = max(2000, math.ceil(vmax / 4 / 1000) * 1000)  # höchstens ~5 Gitterlinien
        self._grid(cr, left, y0, pw, ph, fg, 0, vmax, step, "RPM")
        for key in hw.fan_keys():
            pts = [(e[0], e[3].get(key)) for e in self.history if e[3].get(key) is not None]
            self._line(cr, left, y0, pw, ph, 0, vmax, pts, fan_color(key))

        y = height - 6
        draw_text(self, cr, left, y, "−5 min", fg, 0.5)
        draw_text(self, cr, left + pw, y, "jetzt", fg, 0.5, "right")


# --------------------------------------------------------------------------- #
# Kurven-Editor
# --------------------------------------------------------------------------- #

class CurveEditor(Gtk.DrawingArea):
    """Punkte per Maus ziehen. x = Temperatur, y = Drehzahl in %."""

    PAD_L, PAD_R, PAD_T, PAD_B = 44, 16, 14, 30
    T0, T1 = hw.TEMP_MIN, hw.TEMP_MAX

    def __init__(self, on_change, on_select):
        super().__init__()
        self.points = None
        self.factory = None
        self.live_temp = None
        self.selected = None
        self.editable = True
        self.on_change = on_change
        self.on_select = on_select
        self._drag = None
        self.set_content_height(300)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        g = Gtk.GestureDrag.new()
        g.connect("drag-begin", self._begin)
        g.connect("drag-update", self._update)
        g.connect("drag-end", lambda *_: setattr(self, "_drag", None))
        self.add_controller(g)

    def _box(self):
        w, h = self.get_width(), self.get_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def _to_xy(self, t, pwm):
        x0, y0, w, h = self._box()
        return (x0 + (t - self.T0) / (self.T1 - self.T0) * w,
                y0 + h - pwm / hw.PWM_MAX * h)

    def _from_xy(self, x, y):
        x0, y0, w, h = self._box()
        return (self.T0 + (x - x0) / w * (self.T1 - self.T0),
                (y0 + h - y) / h * hw.PWM_MAX)

    def clamp_point(self, i, t, pwm):
        pts = self.points
        tmin = pts[i - 1][0] + 1 if i > 0 else self.T0
        tmax = pts[i + 1][0] - 1 if i < len(pts) - 1 else self.T1
        wmin = pts[i - 1][1] if i > 0 else 0
        wmax = pts[i + 1][1] if i < len(pts) - 1 else hw.PWM_MAX
        return int(round(min(max(t, tmin), tmax))), int(round(min(max(pwm, wmin), wmax)))

    def _begin(self, _g, x, y):
        if not self.points:
            return
        best, bd = None, 18 ** 2
        for i, (t, w) in enumerate(self.points):
            px, py = self._to_xy(t, w)
            d = (px - x) ** 2 + (py - y) ** 2
            if d < bd:
                best, bd = i, d
        if best is None:
            return
        self.selected = best
        self.on_select(best)
        if self.editable:
            self._drag = (best, x, y)
        self.queue_draw()

    def _update(self, _g, dx, dy):
        if not self._drag:
            return
        i, sx, sy = self._drag
        t, pwm = self._from_xy(sx + dx, sy + dy)
        self.points[i] = list(self.clamp_point(i, t, pwm))
        self.on_select(i)
        self.on_change()
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        fg = fg_color(self)
        x0, y0, w, h = self._box()
        cr.set_line_width(1)
        for pct in range(0, 101, 20):
            _, y = self._to_xy(self.T0, hw.pct_to_pwm(pct))
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(x0, y)
            cr.line_to(x0 + w, y)
            cr.stroke()
            draw_text(self, cr, x0 - 6, y, f"{pct} %", fg, 0.6, "right")
        for t in range(self.T0, self.T1 + 1, 10):
            x, _ = self._to_xy(t, 0)
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(x, y0)
            cr.line_to(x, y0 + h)
            cr.stroke()
            draw_text(self, cr, x, y0 + h + 14, f"{t}°", fg, 0.6, "center")

        if self.live_temp is not None:
            x, _ = self._to_xy(min(max(self.live_temp, self.T0), self.T1), 0)
            cr.set_source_rgba(*COL["warn"], 0.55)
            cr.set_dash([4, 3])
            cr.move_to(x, y0)
            cr.line_to(x, y0 + h)
            cr.stroke()
            cr.set_dash([])
            draw_text(self, cr, x + 4, y0 + 8, f"jetzt {self.live_temp:.0f} °C", COL["warn"], 0.9)

        if self.factory:
            cr.set_source_rgba(*COL["factory"], 0.9)
            cr.set_line_width(1.5)
            cr.set_dash([5, 4])
            for i, (t, pwm) in enumerate(self.factory):
                x, y = self._to_xy(min(max(t, self.T0), self.T1), pwm)
                (cr.move_to if i == 0 else cr.line_to)(x, y)
            cr.stroke()
            cr.set_dash([])

        if not self.points:
            return
        alpha = 1.0 if self.editable else 0.45
        cr.set_source_rgba(*COL["accent"], alpha)
        cr.set_line_width(2.5)
        for i, (t, pwm) in enumerate(self.points):
            x, y = self._to_xy(t, pwm)
            (cr.move_to if i == 0 else cr.line_to)(x, y)
        cr.stroke()
        for i, (t, pwm) in enumerate(self.points):
            x, y = self._to_xy(t, pwm)
            cr.arc(x, y, 7 if i == self.selected else 5, 0, 2 * math.pi)
            cr.set_source_rgba(*COL["accent"], alpha)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.set_line_width(1.5)
            cr.stroke()


# --------------------------------------------------------------------------- #
# Helfer-Aufruf
# --------------------------------------------------------------------------- #

def helper_available():
    return os.path.exists(hw.HELPER)


def cuda_available():
    import ctypes.util
    return hw.find_nvidia_gpu() is not None and ctypes.util.find_library("cuda") is not None


# --------------------------------------------------------------------------- #
# Einrichtungsassistent
# --------------------------------------------------------------------------- #

class SetupDialog(Adw.Dialog):
    def __init__(self, win, first_run=False):
        super().__init__(title="Gerät einrichten", content_width=600, content_height=700)
        self.win = win
        self.first_run = first_run
        self.running = False
        self.finished = False
        self.error = None
        self.proc = None
        self.comp = hw.compatibility()
        self.connect("close-attempt", self._on_close_attempt)

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        self.stack.add_named(self._page_start(), "start")
        self.stack.add_named(self._page_run(), "run")
        self.stack.add_named(self._page_done(), "done")
        view.set_content(self.stack)
        self.toasts.set_child(view)
        self.set_child(self.toasts)

    # ------------------------------------------------------------- Startseite

    def _page_start(self):
        page = Adw.PreferencesPage()
        c = self.comp
        dmi = c["dmi"]
        name = " ".join(x for x in (dmi["sys_vendor"].replace("COMPUTER INC.", "").strip(),
                                    dmi["product_name"]) if x) or "Unbekanntes Gerät"
        verdict = hw.VERDICT_NAMES[c["verdict"]]
        tested = dmi["board_name"] == "G614JI"
        desc = (f"BIOS {dmi['bios_version']} · Kernel {c['kernel']}\n"
                f"Ergebnis der Erkennung: {verdict}. ")
        desc += ("Das ist das Gerät, auf dem die App getestet wurde." if tested else
                 "Die App wurde nur auf einem ROG Strix G16 G614JI getestet. Auf diesem Gerät "
                 "können sich Lüfter und Firmware anders verhalten – die Einrichtung prüft das.")
        g = Adw.PreferencesGroup(title=name, description=desc)
        for f in c["fans"]:
            caps = []
            caps.append(f"Kurve mit {f['curve_points']} Punkten" if f["curve_points"] >= 2 else "keine Kurve")
            caps.append("Vollgas" if f["fullspeed"] else "kein Vollgas")
            caps.append("Drehzahl lesbar" if f["has_rpm"] else "keine Drehzahl")
            r = Adw.ActionRow(title=hw.fan_name(f["key"]), subtitle=" · ".join(caps))
            r.add_prefix(Gtk.Image(icon_name="emblem-system-symbolic"))
            g.add(r)
        if not c["fans"]:
            g.add(Adw.ActionRow(title="Keine Lüfter erkannt",
                                subtitle="asus-wmi stellt auf diesem Gerät keine Lüfter bereit."))
        r = Adw.ActionRow(title="Energieprofile",
                          subtitle=", ".join(hw.profile_name(p) for p in c["profiles"]))
        r.add_prefix(Gtk.Image(icon_name="power-profile-balanced-symbolic"))
        g.add(r)
        r = Adw.ActionRow(title="Leistungslimits",
                          subtitle=", ".join(hw.LIMITS[n][0] for n in c["limits"]) or "nicht verfügbar")
        r.add_prefix(Gtk.Image(icon_name="power-profile-performance-symbolic"))
        g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(
            title="Was soll eingerichtet werden?",
            description="Die Erkennung und ein harmloser Test der Leistungslimits laufen immer. "
                        "Deine Einstellungen bleiben erhalten.")
        multi = len(c["profiles"]) > 1 and any(f["curve_points"] >= 2 for f in c["fans"])
        self.opt_factory = Adw.SwitchRow(
            title="Werkskurven aller Profile einlesen",
            subtitle="Schaltet kurz durch alle Energieprofile (ca. 10 s). Danach ist die "
                     "Werkskurve im Kurven-Editor für jedes Profil verfügbar.",
            active=multi, sensitive=multi)
        g.add(self.opt_factory)
        can_measure = any(f["fullspeed"] and f["has_rpm"] for f in c["fans"])
        self.opt_maxrpm = Adw.SwitchRow(
            title="Höchstdrehzahl der Lüfter messen",
            subtitle="Jeder Lüfter läuft nacheinander bis zu 25 s auf Vollgas – das ist laut. "
                     "Prüft auch, ob Vollgas wirkt, und erkennt falsche Drehzahlwerte.",
            active=False, sensitive=can_measure)
        g.add(self.opt_maxrpm)
        page.add(g)

        g = Adw.PreferencesGroup()
        pw = Adw.ExpanderRow(title=PASSWORD_TITLE,
                             subtitle="Die Einrichtung fragt einmal nach dem Administrator-Passwort.")
        pw.add_prefix(Gtk.Image(icon_name="dialog-password-symbolic"))
        lbl = Gtk.Label(label=PASSWORD_TEXT, wrap=True, xalign=0, css_classes=["dim-label"])
        pw.add_row(self._wrap_row(lbl))
        g.add(pw)
        page.add(g)

        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=18)
        skip = Gtk.Button(label="Überspringen" if self.first_run else "Abbrechen",
                          css_classes=["pill"])
        skip.connect("clicked", self._on_skip)
        start = Gtk.Button(label="Einrichtung starten", css_classes=["pill", "suggested-action"],
                           sensitive=c["verdict"] != "incompatible")
        start.connect("clicked", self._on_start)
        box.append(skip)
        box.append(start)
        g = Adw.PreferencesGroup()
        g.add(box)
        page.add(g)
        return page

    @staticmethod
    def _wrap_row(widget):
        row = Adw.PreferencesRow(activatable=False)
        row.set_child(padded(widget, 12, 14))
        return row

    # ------------------------------------------------------------- Fortschritt

    def _page_run(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        padded(box, 24, 24)
        self.step_lbl = Gtk.Label(label="Starte …", xalign=0, wrap=True, css_classes=["title-3"])
        self.bar = Gtk.ProgressBar(show_text=False)
        self.log_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.log_list)
        self.log_scroll = sw
        hint = Gtk.Label(label="Bitte das Fenster offen lassen. Die Einrichtung kann bis zu "
                               "zwei Minuten dauern.", xalign=0, wrap=True,
                         css_classes=["dim-label", "caption"])
        for w in (self.step_lbl, self.bar, hint, sw):
            box.append(w)
        return box

    def _add_log(self, text):
        row = Adw.ActionRow(title=GLib.markup_escape_text(text), title_lines=0)
        self.log_list.append(row)
        adj = self.log_scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    # ------------------------------------------------------------- Ergebnis

    def _page_done(self):
        self.status = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="Einrichtung abgeschlossen")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.result_group = Adw.PreferencesGroup()
        box.append(self.result_group)
        btns = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12)
        copy = Gtk.Button(label="Gerätebericht kopieren", css_classes=["pill"])
        copy.connect("clicked", lambda *_: self._copy_report())
        done = Gtk.Button(label="Fertig", css_classes=["pill", "suggested-action"])
        done.connect("clicked", lambda *_: self.force_close())
        btns.append(copy)
        btns.append(done)
        box.append(btns)
        self.status.set_child(box)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.status)
        return sw

    def _fill_result(self):
        dev = hw.load_device() or {}
        if self.error:
            self.status.set_icon_name("dialog-warning-symbolic")
            self.status.set_title("Einrichtung nicht vollständig")
            self.status.set_description(self.error)
        else:
            self.status.set_description(
                "Die Ergebnisse sind gespeichert. Du kannst die Einrichtung jederzeit über das "
                "Menü wiederholen.")
        for f in hw.detect_fans():
            d = (dev.get("fans") or {}).get(f["key"], {})
            if d.get("max_rpm"):
                sub = f"Höchstdrehzahl {d['max_rpm']} U/min"
            elif d.get("fullspeed_effective") is False:
                sub = "Vollgas zeigt keine Wirkung"
            else:
                sub = "Höchstdrehzahl nicht gemessen"
            if f["key"] in ((dev.get("quirks") or {}).get("rpm_bit15_fans") or []):
                sub += " · meldet bei Vollgas ein Statusbit (wird korrigiert)"
            self.result_group.add(Adw.ActionRow(title=hw.fan_name(f["key"]), subtitle=sub))
        q = (dev.get("quirks") or {}).get("ppt_needs_curve")
        self.result_group.add(Adw.ActionRow(
            title="Leistungslimits",
            subtitle={True: "Firmware verlangt eine aktive Kurve – die App umgeht das automatisch.",
                      False: "Lassen sich direkt ändern.",
                      None: "Nicht geprüft."}[q]))
        fp = dev.get("factory_profiles") or []
        self.result_group.add(Adw.ActionRow(
            title="Werkskurven",
            subtitle=("Gesichert für: " + ", ".join(hw.profile_name(p) for p in fp)) if fp
            else "Noch nicht eingelesen."))

    # ------------------------------------------------------------- Ablauf

    def _on_skip(self, *_):
        if self.first_run:
            s = load_ui_settings()
            s["setup_skipped"] = True
            save_ui_settings(s)
        self.force_close()

    def _on_close_attempt(self, *_):
        if self.running:
            self.toasts.add_toast(Adw.Toast.new("Bitte warten, bis die Einrichtung fertig ist."))
        else:
            self.force_close()

    def _on_start(self, *_):
        if not helper_available():
            self.toasts.add_toast(Adw.Toast.new("Helfer nicht gefunden. Bitte die App neu starten."))
            return
        opts = []
        if self.opt_factory.get_active():
            opts.append("factory")
        if self.opt_maxrpm.get_active():
            opts.append("maxrpm")
        try:
            self.proc = Gio.Subprocess.new(
                ["pkexec", hw.HELPER, "setup", *opts],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.toasts.add_toast(Adw.Toast.new(f"pkexec nicht startbar: {e.message}"))
            return
        self.running = True
        self.set_can_close(False)
        self.stack.set_visible_child_name("run")
        self.stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)
        self.proc.wait_async(None, self._on_exit)

    def _on_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            return
        line = line.strip()
        if line:
            try:
                msg = json.loads(line) if line.startswith("{") else {"log": line}
            except ValueError:
                msg = {"log": line}
            if "progress" in msg:
                self.bar.set_fraction(max(0.0, min(1.0, float(msg["progress"]))))
            if "step" in msg:
                self.step_lbl.set_label(msg["step"])
            if "log" in msg:
                self._add_log(msg["log"])
            if "error" in msg:
                self.error = msg["error"]
                self._add_log("Fehler: " + msg["error"])
            if msg.get("done"):
                self.finished = True
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line)

    def _on_exit(self, proc, res):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        code = proc.get_exit_status()
        self.running = False
        self.set_can_close(True)
        if code == 126:
            self.stack.set_visible_child_name("start")
            self.toasts.add_toast(Adw.Toast.new("Abgebrochen: Passwortabfrage wurde geschlossen"))
            return
        if code == 127:
            self.error = "Keine Berechtigung (pkexec hat abgelehnt)."
        elif code != 0 and not self.error:
            self.error = f"Der Helfer hat mit Code {code} beendet."
        elif code == 0 and not self.finished and not self.error:
            self.error = "Die Einrichtung wurde nicht vollständig abgeschlossen."
        if not self.error:
            s = load_ui_settings()
            s["setup_skipped"] = False
            save_ui_settings(s)
        self._fill_result()
        self.stack.set_visible_child_name("done")
        self.win.on_setup_finished()

    def _copy_report(self):
        self.get_clipboard().set(hw.device_report())
        self.toasts.add_toast(Adw.Toast.new("Gerätebericht in die Zwischenablage kopiert"))


class ReportDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title="Gerätebericht", content_width=640, content_height=620)
        text = hw.device_report()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Kopieren")
        toasts = Adw.ToastOverlay()

        def do_copy(*_):
            self.get_clipboard().set(text)
            toasts.add_toast(Adw.Toast.new("In die Zwischenablage kopiert"))
        copy.connect("clicked", do_copy)
        header.pack_start(copy)
        view.add_top_bar(header)
        tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        tv.get_buffer().set_text(text)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.set_child(tv)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        info = Gtk.Label(wrap=True, xalign=0, css_classes=["dim-label", "caption"],
                         label="Diesen Bericht kannst du in ein GitHub-Issue kopieren, wenn etwas auf "
                               "deinem Gerät nicht funktioniert. Er enthält nur Hardware- und Treiberdaten.")
        padded(info, 10, 12)
        box.append(info)
        box.append(sw)
        toasts.set_child(box)
        view.set_content(toasts)
        self.set_child(view)


# --------------------------------------------------------------------------- #
# Hauptfenster
# --------------------------------------------------------------------------- #

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="ROG Fan Control")
        self.set_default_size(780, 860)
        self._updating = False
        self.history = deque(maxlen=HISTORY_SECONDS)
        self.gpu_dev = hw.find_nvidia_gpu()
        self.gpu_temp = None
        self.gpu_temp_time = 0
        self._gpu_busy = False
        self._ppd = None
        self.load_proc = None
        self.load_mode = "off"
        self.load_start = 0
        self.load_secs = 0
        self.connect("close-request", lambda *_: self._stop_load() or False)

        self.fans = hw.detect_fans()
        self.curve_keys = hw.curve_fans()
        self.profile_list = hw.profiles()
        self.device = hw.load_device()
        self.saved = hw.load_config()
        self.work = hw.deep_copy(self.saved)
        self.factory = hw.load_factory()
        self.limits_info = hw.read_limits()

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        menu = Gio.Menu()
        menu.append("Gerät einrichten …", "win.setup")
        menu.append("Gerätebericht", "win.report")
        menu.append("Warum ein Passwort?", "win.password")
        menu.append("Über ROG Fan Control", "win.about")
        header.pack_start(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                         tooltip_text="Menü"))
        for name, cb in (("setup", lambda *_: self.open_setup(False)),
                         ("report", lambda *_: ReportDialog().present(self)),
                         ("about", lambda *_: self._about()),
                         ("password", lambda *_: self._password_info())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self.apply_btn = Gtk.Button(label="Anwenden", css_classes=["suggested-action"])
        self.apply_btn.connect("clicked", self._apply)
        self.revert_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Änderungen verwerfen")
        self.revert_btn.connect("clicked", self._revert)
        header.pack_end(self.apply_btn)
        header.pack_end(self.revert_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner(button_label="Dienst aktivieren")
        self.banner.connect("button-clicked", lambda *_: self._run_helper(
            ["enable-service"], ok="Hintergrunddienst läuft", after=self._refresh_hints))
        view.add_top_bar(self.banner)

        self.stack.add_titled_with_icon(self._page_overview(), "overview", "Übersicht",
                                        "utilities-system-monitor-symbolic")
        self.stack.add_titled_with_icon(self._page_curves(), "curves", "Lüfterkurven",
                                        "document-edit-symbolic")
        self.stack.add_titled_with_icon(self._page_limits(), "limits", "Leistung",
                                        "power-profile-performance-symbolic")
        view.set_content(self.stack)

        prof = hw.read_profile()
        cur = prof if prof in self.profile_list else self.profile_list[0]
        self._updating = True
        self.profile_toggle.set_active_name(cur)
        self._updating = False
        if self.curve_keys:
            self.curve_profile.set_active_name(cur)
            self.curve_fan.set_active_name(self.curve_keys[0])
        if self.limit_rows:
            self.limit_profile.set_active_name(cur)

        self._init_ppd()
        self._apply_device()
        self._update_dirty()
        self._load_curve_editor()
        self._load_limits()
        self._tick()
        self._refresh_hints()
        GLib.timeout_add_seconds(1, self._tick)
        GLib.timeout_add_seconds(5, self._refresh_hints)

        if self.device is None and not load_ui_settings().get("setup_skipped"):
            GLib.timeout_add(600, lambda: self.open_setup(True) or False)

    # ------------------------------------------------------------------ Einrichtung

    def open_setup(self, first_run):
        SetupDialog(self, first_run).present(self)

    def on_setup_finished(self):
        self.device = hw.load_device()
        self.factory = hw.load_factory()
        self._apply_device()
        self._load_curve_editor()
        self._refresh_hints()

    def _apply_device(self):
        hints = [hw.max_rpm(k, self.device) or 0 for k in hw.fan_keys()]
        self.graph.rpm_hint = max(hints, default=0)

    def _password_info(self):
        d = Adw.AlertDialog(heading=PASSWORD_TITLE, body=PASSWORD_TEXT)
        d.add_response("ok", "Verstanden")
        d.present(self)

    def _about(self):
        about = Adw.AboutDialog(
            application_name="ROG Fan Control", application_icon=APP_ID, version=hw.APP_VERSION,
            developer_name="ms-star1", license_type=Gtk.License.MIT_X11, website=hw.REPO_URL,
            issue_url=hw.REPO_URL + "/issues",
            comments="Lüfterkurven, Vollgas, Energieprofile und Leistungslimits für ASUS-ROG-Laptops.\n\n"
                     f"Nur getestet auf: {hw.TESTED_DEVICE}.\n\n"
                     "Änderungen brauchen ein Administrator-Passwort, weil die Firmware-"
                     "Schnittstellen nur root beschreiben darf (Details im Menü).")
        about.present(self)

    # ------------------------------------------------------------------ Seiten

    def _toggle_group(self, items):
        tg = Adw.ToggleGroup(homogeneous=True)
        for name, label in items:
            tg.add(Adw.Toggle(name=name, label=label))
        return tg

    def _profile_toggle_group(self):
        return self._toggle_group([(p, hw.profile_name(p)) for p in self.profile_list])

    def _row_with(self, widget):
        row = Adw.PreferencesRow(activatable=False)
        row.set_child(padded(widget))
        return row

    def _page_overview(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Verlauf", description="Die letzten 5 Minuten")
        legend = Gtk.Label(xalign=0, use_markup=True, wrap=True, css_classes=["caption"])
        items = [(COL["cpu_temp"], "CPU-Temp.")]
        if self.gpu_dev:
            items.append((COL["gpu_temp"], "GPU-Temp."))
        items += [(fan_color(f["key"]), hw.fan_name(f["key"])) for f in self.fans if f["has_rpm"]]
        legend.set_markup("   ".join(f"<span foreground='{hexcol(c)}'>●</span> {GLib.markup_escape_text(t)}"
                                     for c, t in items))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card"])
        self.graph = LiveGraph(self.history)
        self.graph.set_margin_top(12)
        self.graph.set_margin_start(8)
        self.graph.set_margin_end(12)
        legend.set_margin_start(12)
        legend.set_margin_bottom(10)
        box.append(self.graph)
        box.append(legend)
        g.add(box)
        page.add(g)

        g = Adw.PreferencesGroup(title="Energieprofil",
                                 description="Entspricht Fn+F5 und dem GNOME-Energiemodus. "
                                             "Die eigenen Kurven und Limits des Profils werden automatisch gesetzt.")
        self.profile_toggle = self._profile_toggle_group()
        self.profile_toggle.connect("notify::active-name", self._on_profile_toggled)
        g.add(self._row_with(self.profile_toggle))
        g.set_visible(self.profile_list != ["default"])
        page.add(g)

        g = Adw.PreferencesGroup(title="Lüfter",
                                 description="Der Schalter setzt einen Lüfter sofort auf Vollgas. "
                                             "Das gilt bis zum Ausschalten oder Neustart.")
        self.fan_rows = {}
        self.fan_switch = {}
        for f in self.fans:
            if f["fullspeed"]:
                r = Adw.SwitchRow(title=f"{hw.fan_name(f['key'])} · Vollgas")
                r.connect("notify::active", self._on_fullspeed, f["key"])
                self.fan_switch[f["key"]] = r
            else:
                r = Adw.ActionRow(title=hw.fan_name(f["key"]))
            self.fan_rows[f["key"]] = r
            g.add(r)
        if not self.fans:
            g.add(Adw.ActionRow(title="Keine Lüfter erkannt"))
        page.add(g)

        g = Adw.PreferencesGroup(
            title="Lasttest",
            description="Erzeugt Volllast, um Lüfterkurven und Limits zu prüfen. Temperaturen und "
                        "Drehzahlen siehst du oben im Verlauf. Die Firmware schützt weiterhin vor "
                        "Überhitzung. Der Test stoppt automatisch und beim Schließen der App.")
        self.load_toggle = self._toggle_group(LOAD_MODES)
        self.load_toggle.set_active_name("off")
        if not cuda_available():
            for name in ("gpu", "all"):
                self.load_toggle.get_toggle_by_name(name).set_enabled(False)
        self.load_toggle.connect("notify::active-name", self._on_load_toggled)
        g.add(self._row_with(self.load_toggle))
        self.load_minutes = Adw.SpinRow.new_with_range(1, 30, 1)
        self.load_minutes.set_title("Automatisch stoppen nach (Minuten)")
        self.load_minutes.set_value(5)
        g.add(self.load_minutes)
        self.load_status = Adw.ActionRow(title="Status", subtitle="Kein Lasttest aktiv")
        g.add(self.load_status)
        if not cuda_available():
            g.add(Adw.ActionRow(title="GPU-Last nicht verfügbar", subtitle_lines=0,
                                subtitle="Dafür wird eine NVIDIA-GPU mit proprietärem Treiber (libcuda) benötigt."))
        page.add(g)

        g = Adw.PreferencesGroup(title="Temperaturen")
        self.cpu_temp_row = Adw.ActionRow(title="CPU (Package)")
        self.cpu_temp_lbl = Gtk.Label(css_classes=["numeric", "title-4"])
        self.cpu_temp_row.add_suffix(self.cpu_temp_lbl)
        g.add(self.cpu_temp_row)
        self.gpu_temp_row = Adw.ActionRow(title="GPU (NVIDIA)", visible=bool(self.gpu_dev))
        self.gpu_temp_lbl = Gtk.Label(css_classes=["numeric", "title-4"])
        self.gpu_temp_row.add_suffix(self.gpu_temp_lbl)
        g.add(self.gpu_temp_row)
        page.add(g)

        self.hint_group = Adw.PreferencesGroup(title="Hinweise")
        self.hint_rows = []
        page.add(self.hint_group)
        return page

    def _page_curves(self):
        if not self.curve_keys:
            return Adw.StatusPage(icon_name="dialog-information-symbolic",
                                  title="Keine Lüfterkurven",
                                  description="Dieses Gerät bietet keine Schnittstelle für eigene "
                                              "Lüfterkurven (asus_custom_fan_curve).")
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Kurve bearbeiten",
            description="Jedes Profil hat eigene Kurven. Punkte mit der Maus ziehen. "
                        "Die gestrichelte Linie ist die Werkskurve, die rote Linie die aktuelle Temperatur.")
        self.curve_profile = self._profile_toggle_group()
        self.curve_fan = self._toggle_group([(k, hw.fan_name(k)) for k in self.curve_keys])
        for tg in (self.curve_profile, self.curve_fan):
            tg.connect("notify::active-name", lambda *_: self._load_curve_editor())
            row = self._row_with(tg)
            g.add(row)
            if tg is self.curve_profile and self.profile_list == ["default"]:
                row.set_visible(False)
        self.curve_enable = Adw.SwitchRow(title="Eigene Kurve verwenden",
                                          subtitle="Aus: Die Firmware regelt mit ihrer Werkskurve.")
        self.curve_enable.connect("notify::active", self._on_curve_enable)
        g.add(self.curve_enable)
        page.add(g)

        g = Adw.PreferencesGroup()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        self.editor = CurveEditor(self._on_editor_change, self._on_editor_select)
        self.editor.set_margin_top(10)
        self.editor.set_margin_bottom(6)
        self.editor.set_margin_start(6)
        self.editor.set_margin_end(10)
        card.append(self.editor)
        g.add(card)
        page.add(g)

        g = Adw.PreferencesGroup(title="Ausgewählter Punkt")
        self.pt_temp = Adw.SpinRow.new_with_range(hw.TEMP_MIN, hw.TEMP_MAX, 1)
        self.pt_temp.set_title("Temperatur (°C)")
        self.pt_pct = Adw.SpinRow.new_with_range(0, 100, 1)
        self.pt_pct.set_title("Drehzahl (%)")
        self.pt_temp.connect("notify::value", self._on_point_spin)
        self.pt_pct.connect("notify::value", self._on_point_spin)
        g.add(self.pt_temp)
        g.add(self.pt_pct)
        page.add(g)

        g = Adw.PreferencesGroup(title="Vorlagen")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                           column_spacing=8, row_spacing=8, homogeneous=True)
        for name in PRESETS:
            b = Gtk.Button(label=name)
            b.connect("clicked", self._on_preset, name)
            flow.append(b)
        self.factory_btn = Gtk.Button(label="Werkskurve")
        self.factory_btn.connect("clicked", self._on_preset, None)
        flow.append(self.factory_btn)
        self.copy_btn = Gtk.Button(label="Auf alle Lüfter")
        self.copy_btn.connect("clicked", self._on_copy_all)
        flow.append(self.copy_btn)
        g.add(flow)
        self.curve_status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"])
        self.curve_status.set_margin_top(10)
        g.add(self.curve_status)
        page.add(g)
        return page

    def _page_limits(self):
        self.limit_rows = {}
        if not self.limits_info:
            return Adw.StatusPage(icon_name="dialog-information-symbolic",
                                  title="Keine Leistungslimits",
                                  description="Dieses Gerät stellt über asus-armoury keine "
                                              "Leistungslimits bereit.")
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Leistungslimits pro Profil",
            description="Weniger Leistung heißt weniger Wärme und leisere Lüfter. "
                        "„Standard“ heißt: Firmware-Standardwert. Die Limits werden bei jedem Profilwechsel gesetzt.")
        self.limit_profile = self._profile_toggle_group()
        self.limit_profile.connect("notify::active-name", lambda *_: self._load_limits())
        row = self._row_with(self.limit_profile)
        row.set_visible(self.profile_list != ["default"])
        g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup()
        for name, (title, unit) in hw.LIMITS.items():
            info = self.limits_info.get(name)
            if not info:
                continue
            r = Adw.SpinRow.new_with_range(info["min"], info["max"], 1)
            r.set_title(f"{title} ({unit})")
            btn = Gtk.Button(label="Standard", valign=Gtk.Align.CENTER, css_classes=["flat"])
            btn.connect("clicked", self._on_limit_default, name)
            r.add_suffix(btn)
            r.connect("notify::value", self._on_limit_spin, name)
            self.limit_rows[name] = r
            g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(title="Aktuell gesetzt")
        self.limit_now = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        g.add(self.limit_now)
        page.add(g)
        return page

    # ------------------------------------------------------------------ Live

    def _tick(self):
        rpms = {f["fan"]: f["rpm"] for f in hw.read_fans()}
        cpu = hw.read_cpu_temp()
        gpu_state = hw.gpu_runtime_status(self.gpu_dev)
        if gpu_state == "active":
            if time.monotonic() - self.gpu_temp_time > 3:
                self._poll_gpu()
        else:
            self.gpu_temp = None
        self.history.append((time.monotonic(), cpu, self.gpu_temp, rpms))
        self.graph.queue_draw()

        self.cpu_temp_lbl.set_label(f"{cpu:.0f} °C" if cpu is not None else "–")
        if gpu_state == "active":
            self.gpu_temp_lbl.set_label(f"{self.gpu_temp:.0f} °C" if self.gpu_temp is not None else "…")
            self.gpu_temp_row.set_subtitle("")
        else:
            self.gpu_temp_lbl.set_label("–")
            self.gpu_temp_row.set_subtitle("Im Energiesparmodus: wird nicht abgefragt, damit sie schlafen kann")

        state = hw.read_state()["fullspeed"]
        self._updating = True
        for key, row in self.fan_rows.items():
            rpm = rpms.get(key)
            if rpm is None:
                sub = "Drehzahl nicht verfügbar"
            else:
                mx = hw.max_rpm(key, self.device)
                sub = f"{rpm} U/min" + (f" · {min(100, round(rpm * 100 / mx))} %" if mx else "")
            row.set_subtitle(sub)
            sw = self.fan_switch.get(key)
            if sw is not None and sw.get_active() != state.get(key, False):
                sw.set_active(state.get(key, False))
        prof = hw.read_profile()
        if prof in self.profile_list and self.profile_toggle.get_active_name() != prof:
            self.profile_toggle.set_active_name(prof)
        self._updating = False

        if self.curve_keys:
            self.editor.live_temp = self.gpu_temp if self.curve_fan.get_active_name() == "gpu" else cpu
            self.editor.queue_draw()
        if self.load_proc is not None:
            el = int(time.monotonic() - self.load_start)
            self.load_status.set_subtitle(
                f"{dict(LOAD_MODES)[self.load_mode]}-Last läuft seit {el // 60}:{el % 60:02d} "
                f"von {self.load_secs // 60}:{self.load_secs % 60:02d} min")
        if self.limit_rows:
            info = hw.read_limits()
            self.limit_now.set_label(" · ".join(
                f"{hw.LIMITS[n][0]}: {v['current']} {hw.LIMITS[n][1]}" for n, v in info.items()))
        return True

    def _poll_gpu(self):
        if self._gpu_busy:
            return
        self._gpu_busy = True
        try:
            proc = Gio.Subprocess.new(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self._gpu_busy = False
            return

        def done(p, res):
            self._gpu_busy = False
            self.gpu_temp_time = time.monotonic()
            try:
                _, out, _ = p.communicate_utf8_finish(res)
                self.gpu_temp = float(out.strip().splitlines()[0])
            except (GLib.Error, ValueError, IndexError):
                self.gpu_temp = None
        proc.communicate_utf8_async(None, None, done)

    # ------------------------------------------------------------------ Hinweise

    def _refresh_hints(self):
        for r in self.hint_rows:
            self.hint_group.remove(r)
        self.hint_rows = []
        units = ["rog-fan-control.service", "thermald.service", "tuned-ppd.service",
                 "power-profiles-daemon.service", "asusd.service", "nvidia-powerd.service"]
        st = systemctl_active(units)
        hints = []

        if st["rog-fan-control.service"] != "active":
            self.banner.set_title("Der Hintergrunddienst läuft nicht. Kurven gehen nach einem "
                                  "Profilwechsel oder Neustart verloren.")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        if self.device is None:
            hints.append(("dialog-information-symbolic", "Gerät noch nicht eingerichtet",
                          "Über Menü → „Gerät einrichten …“ liest die App die Werkskurven aller "
                          "Profile ein und kann die Höchstdrehzahlen messen."))
        if st["asusd.service"] == "active":
            hints.append(("dialog-warning-symbolic", "asusd (asusctl) läuft",
                          "asusd setzt ebenfalls Lüfterkurven. Beide Programme überschreiben sich "
                          "gegenseitig. Verwende nur eines."))
        if st["tuned-ppd.service"] == "active" and st["power-profiles-daemon.service"] == "active":
            hints.append(("dialog-warning-symbolic", "Zwei Profil-Dienste aktiv",
                          "tuned-ppd und power-profiles-daemon laufen gleichzeitig und streiten "
                          "sich um das Energieprofil."))
        elif "active" in (st["tuned-ppd.service"], st["power-profiles-daemon.service"]):
            which = "tuned-ppd" if st["tuned-ppd.service"] == "active" else "power-profiles-daemon"
            hints.append(("emblem-ok-symbolic", f"{which} steuert das Energieprofil",
                          "Wechsel über GNOME oder Fn+F5 werden erkannt. Dabei schaltet die Firmware "
                          "eigene Kurven ab, und der Dienst setzt sie sofort neu."))
        if st["thermald.service"] == "active":
            hints.append(("dialog-information-symbolic", "thermald ist aktiv",
                          "thermald kann die CPU bei Hitze unabhängig von den Lüftern drosseln. "
                          "Das ist ein Schutz und meist sinnvoll, kann aber Leistungslimits unterbieten."))

        prof = hw.read_profile()
        if prof in self.saved["profiles"] and st["rog-fan-control.service"] == "active":
            pc = self.saved["profiles"][prof]["curves"]
            off = [hw.fan_name(k) for k in self.curve_keys
                   if pc.get(k, {}).get("enabled") and not (hw.read_curve(k) or {}).get("enabled")]
            if off:
                hints.append(("dialog-warning-symbolic", "Eigene Kurve gerade nicht aktiv",
                              f"Für {', '.join(off)} ist eine eigene Kurve gespeichert, aber die "
                              "Firmware nutzt gerade ihre eigene. Der Dienst setzt sie in wenigen "
                              "Sekunden neu."))
        if any(self.saved["profiles"][p]["limits"].get("nv_dynamic_boost") is not None
               for p in self.saved["profiles"]) and st["nvidia-powerd.service"] != "active":
            hints.append(("dialog-warning-symbolic", "nvidia-powerd läuft nicht",
                          "Ohne nvidia-powerd hat das Dynamic-Boost-Limit keine Wirkung."))
        fs = [hw.fan_name(k) for k, on in hw.read_state()["fullspeed"].items() if on]
        if fs:
            hints.append(("dialog-information-symbolic", "Vollgas aktiv",
                          f"{', '.join(fs)}: läuft auf Vollgas. Kurven wirken dort erst nach "
                          "dem Abschalten."))
        dev_fans = (self.device or {}).get("fans") or {}
        weak = [hw.fan_name(k) for k, d in dev_fans.items()
                if isinstance(d, dict) and d.get("max_rpm") and d["max_rpm"] < 1500]
        if weak:
            hints.append(("dialog-warning-symbolic", "Lüfter auffällig langsam",
                          f"{', '.join(weak)} erreichte bei der Messung unter 1500 U/min. "
                          "Eventuell blockiert oder verschmutzt."))
        if not hints:
            hints.append(("emblem-ok-symbolic", "Keine Konflikte gefunden", ""))

        for icon, title, sub in hints:
            r = Adw.ActionRow(title=title, subtitle=sub, subtitle_lines=0)
            r.add_prefix(Gtk.Image(icon_name=icon))
            self.hint_group.add(r)
            self.hint_rows.append(r)
        return True

    # ------------------------------------------------------------------ Lasttest

    def _on_load_toggled(self, tg, _pspec):
        if self._updating:
            return
        mode = tg.get_active_name()
        self._stop_load()
        if mode != "off":
            self._start_load(mode)

    def _set_load_toggle(self, name):
        self._updating = True
        self.load_toggle.set_active_name(name)
        self._updating = False

    def _start_load(self, mode):
        secs = int(self.load_minutes.get_value()) * 60
        try:
            proc = Gio.Subprocess.new([LOAD_TOOL, mode, str(secs)],
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.toast(f"Lasttest nicht startbar: {e.message}")
            self._set_load_toggle("off")
            return
        self.load_proc, self.load_mode = proc, mode
        self.load_start, self.load_secs = time.monotonic(), secs
        self.load_status.set_subtitle("Startet …")
        stream = Gio.DataInputStream.new(proc.get_stdout_pipe())

        def on_line(s, res):
            try:
                line, _ = s.read_line_finish_utf8(res)
            except GLib.Error:
                line = None
            if line is None:
                return
            try:
                msg = json.loads(line)
            except ValueError:
                msg = {}
            if "error" in msg:
                self.toast(msg["error"])
            elif msg.get("started") == "gpu":
                self.toast(f"GPU-Last auf {msg.get('device', 'GPU')}")
            elif msg.get("started") == "cpu":
                self.toast(f"CPU-Last auf {msg.get('threads')} Threads")
            s.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, on_line)

        def on_exit(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            if self.load_proc is p:
                self.load_proc = None
                self.load_mode = "off"
                self._set_load_toggle("off")
                self.load_status.set_subtitle("Lasttest beendet")
        proc.wait_async(None, on_exit)

    def _stop_load(self):
        p, self.load_proc = self.load_proc, None
        if p is not None:
            p.send_signal(15)
            self.load_status.set_subtitle("Lasttest gestoppt")
        self.load_mode = "off"

    # ------------------------------------------------------------------ Profil

    def _init_ppd(self):
        if not hw.ppd_mapping_unique():
            return
        for name, path in (hw.PPD_BUS, ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")):
            try:
                proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE,
                                                       None, name, path, name, None)
                if proxy.get_name_owner():
                    self._ppd = (proxy, name)
                    return
            except GLib.Error:
                pass

    def _on_profile_toggled(self, tg, _pspec):
        if self._updating:
            return
        prof = tg.get_active_name()
        if prof == hw.read_profile():
            return
        if self._ppd and prof in hw.PPD_FOR_PROFILE:
            proxy, iface = self._ppd
            try:
                proxy.call_sync("org.freedesktop.DBus.Properties.Set",
                                GLib.Variant("(ssv)", (iface, "ActiveProfile",
                                                       GLib.Variant("s", hw.PPD_FOR_PROFILE[prof]))),
                                Gio.DBusCallFlags.NONE, 3000, None)
                self.toast(f"Profil: {hw.profile_name(prof)}")
                return
            except GLib.Error as e:
                self.toast(f"PowerProfiles-Dienst: {e.message}")
        self._run_helper(["set-profile", prof], ok=f"Profil: {hw.profile_name(prof)}")

    # ------------------------------------------------------------------ Vollgas

    def _on_fullspeed(self, row, _pspec, key):
        if self._updating:
            return
        on = row.get_active()

        def failed():
            self._updating = True
            row.set_active(not on)
            self._updating = False
        self._run_helper(["fullspeed", key, "on" if on else "off"],
                         ok=f"{hw.fan_name(key)}: {'Vollgas' if on else 'Automatik'}",
                         on_fail=failed, after=self._refresh_hints)

    # ------------------------------------------------------------------ Kurven

    def _cur_curve(self):
        p = self.curve_profile.get_active_name() or self.profile_list[0]
        f = self.curve_fan.get_active_name() or self.curve_keys[0]
        return p, f, self.work["profiles"][p]["curves"][f]

    def _fallback_points(self, p, f):
        fac = (self.factory.get(p) or {}).get(f)
        if fac:
            try:
                return hw.validate_curve(fac, hw.n_points(f))
            except ValueError:
                pass
        return preset_points("Ausgewogen", hw.n_points(f))

    def _load_curve_editor(self):
        if not self.curve_keys or not hasattr(self, "editor"):
            return
        p, f, c = self._cur_curve()
        n = hw.n_points(f)
        self._updating = True
        self.curve_enable.set_active(c["enabled"])
        self.curve_enable.set_title(f"Eigene Kurve für {hw.fan_name(f)} im Profil „{hw.profile_name(p)}“")
        self._updating = False
        self.editor.points = c["points"] if c["points"] is not None else self._fallback_points(p, f)
        self.editor.factory = (self.factory.get(p) or {}).get(f)
        self.editor.editable = c["enabled"]
        self.factory_btn.set_sensitive(bool(self.editor.factory))
        self.factory_btn.set_tooltip_text(
            None if self.editor.factory else
            "Werkskurve dieses Profils noch unbekannt. Menü → „Gerät einrichten …“ liest sie ein.")
        same = [k for k in self.curve_keys if hw.n_points(k) == n]
        self.copy_btn.set_sensitive(len(same) > 1)
        self.copy_btn.set_tooltip_text(f"Diese Kurve für alle Lüfter mit {n} Punkten in diesem Profil übernehmen")
        sel = self.editor.selected if self.editor.selected is not None else 0
        self.editor.selected = min(sel, n - 1)
        self._on_editor_select(self.editor.selected)
        self._validate_curve_status()
        self.editor.queue_draw()

    def _on_curve_enable(self, row, _pspec):
        if self._updating:
            return
        p, f, c = self._cur_curve()
        c["enabled"] = row.get_active()
        if c["enabled"] and c["points"] is None:
            c["points"] = [list(x) for x in self.editor.points]
        self.editor.points = c["points"] or self.editor.points
        self.editor.editable = c["enabled"]
        self.editor.queue_draw()
        self._on_editor_select(self.editor.selected or 0)
        self._validate_curve_status()
        self._update_dirty()

    def _on_editor_change(self):
        p, f, c = self._cur_curve()
        c["points"] = self.editor.points
        self._validate_curve_status()
        self._update_dirty()

    def _on_editor_select(self, i):
        if i is None or not self.editor.points:
            return
        t, pwm = self.editor.points[i]
        self._updating = True
        self.pt_temp.set_value(t)
        self.pt_pct.set_value(round(hw.pwm_to_pct(pwm)))
        self.pt_temp.set_subtitle(f"Punkt {i + 1} von {len(self.editor.points)}")
        self.pt_pct.set_subtitle(f"PWM-Wert {pwm}/255")
        en = self.editor.editable
        self.pt_temp.set_sensitive(en)
        self.pt_pct.set_sensitive(en)
        self._updating = False

    def _on_point_spin(self, *_):
        if self._updating or self.editor.selected is None or not self.editor.editable:
            return
        i = self.editor.selected
        t, pwm = self.editor.clamp_point(i, self.pt_temp.get_value(),
                                         hw.pct_to_pwm(self.pt_pct.get_value()))
        self.editor.points[i] = [t, pwm]
        self._on_editor_select(i)
        self._on_editor_change()
        self.editor.queue_draw()

    def _on_preset(self, _btn, name):
        p, f, c = self._cur_curve()
        if name is None:
            pts = [list(x) for x in self.editor.factory]
        else:
            pts = preset_points(name, hw.n_points(f))
        c["points"] = pts
        if not c["enabled"]:
            c["enabled"] = True
            self._updating = True
            self.curve_enable.set_active(True)
            self._updating = False
        self.editor.points = pts
        self.editor.editable = True
        self._on_editor_select(self.editor.selected or 0)
        self._validate_curve_status()
        self._update_dirty()
        self.editor.queue_draw()

    def _on_copy_all(self, _btn):
        p, f, c = self._cur_curve()
        n = hw.n_points(f)
        for other in self.curve_keys:
            if hw.n_points(other) == n:
                self.work["profiles"][p]["curves"][other] = {
                    "enabled": c["enabled"], "points": [list(x) for x in self.editor.points]}
        self._update_dirty()
        self.toast(f"Kurve auf alle passenden Lüfter im Profil „{hw.profile_name(p)}“ übertragen")

    def _validate_curve_status(self):
        p, f, c = self._cur_curve()
        if not c["enabled"]:
            self.curve_status.set_label("Die Firmware regelt diesen Lüfter. Aktiviere die eigene Kurve "
                                        "oder wähle eine Vorlage, um zu bearbeiten.")
            return
        try:
            hw.validate_curve(self.editor.points, hw.n_points(f))
            self.curve_status.set_label("Kurve ist gültig. Mit „Anwenden“ speichern.")
        except ValueError as e:
            self.curve_status.set_label(f"⚠ {e}")

    # ------------------------------------------------------------------ Limits

    def _load_limits(self):
        if not self.limit_rows:
            return
        p = self.limit_profile.get_active_name() or self.profile_list[0]
        lim = self.work["profiles"][p]["limits"]
        self._updating = True
        for name, row in self.limit_rows.items():
            info = self.limits_info[name]
            v = lim.get(name)
            row.set_value(v if v is not None else info["default"])
            row.set_subtitle(f"Standard ({info['default']} {hw.LIMITS[name][1]})" if v is None
                             else f"Eigener Wert · Standard wäre {info['default']}")
        self._updating = False

    def _on_limit_spin(self, row, _pspec, name):
        if self._updating:
            return
        p = self.limit_profile.get_active_name()
        v = int(row.get_value())
        self.work["profiles"][p]["limits"][name] = None if v == self.limits_info[name]["default"] else v
        self._load_limits()
        self._update_dirty()

    def _on_limit_default(self, _btn, name):
        p = self.limit_profile.get_active_name()
        self.work["profiles"][p]["limits"][name] = None
        self._load_limits()
        self._update_dirty()

    # ------------------------------------------------------------------ Anwenden

    def _update_dirty(self):
        dirty = self.work != self.saved
        self.apply_btn.set_sensitive(dirty)
        self.revert_btn.set_sensitive(dirty)

    def _revert(self, *_):
        self.work = hw.deep_copy(self.saved)
        self._load_curve_editor()
        self._load_limits()
        self._update_dirty()

    def _apply(self, *_):
        try:
            cfg = hw.validate_config(self.work, self.limits_info)
        except ValueError as e:
            self.toast(str(e))
            return

        def ok():
            self.saved = hw.load_config()
            self.work = hw.deep_copy(self.saved)
            self._update_dirty()
            self._load_curve_editor()
            self._refresh_hints()
        self._run_helper(["save-config"], stdin=json.dumps(cfg), ok="Gespeichert und angewendet", after=ok)

    def _run_helper(self, args, stdin=None, ok=None, on_fail=None, after=None):
        if not helper_available():
            self.toast("Helfer nicht gefunden (App wurde aktualisiert?). Bitte die App schließen und neu starten.")
            if on_fail:
                on_fail()
            return
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        if stdin is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new(["pkexec", hw.HELPER, *args], flags)
        except GLib.Error as e:
            self.toast(f"pkexec nicht startbar: {e.message}")
            if on_fail:
                on_fail()
            return

        def done(p, res):
            try:
                _, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                out, err = "", e.message
            code = p.get_exit_status()
            if code == 0:
                if ok:
                    self.toast(ok)
            elif code == 126:
                self.toast("Abgebrochen: Passwortabfrage wurde geschlossen")
                if on_fail:
                    on_fail()
            elif code == 127:
                self.toast("Nicht berechtigt: " + ((err or "").strip().splitlines() or ["pkexec verweigert"])[-1][:160])
                if on_fail:
                    on_fail()
            elif code == 2:
                self.toast("Gespeichert, aber mit Fehlern: " + " · ".join((err or out).strip().splitlines())[:200])
            else:
                self.toast((err or out).strip()[:200] or f"Fehler (Code {code})")
                if on_fail:
                    on_fail()
            if after and code not in (126, 127):
                after()
        proc.communicate_utf8_async(stdin, None, done)

    def toast(self, text):
        t = Adw.Toast.new(text)
        t.set_timeout(4)
        self.toasts.add_toast(t)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        win = self.props.active_window or MainWindow(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
