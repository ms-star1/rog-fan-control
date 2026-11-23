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

# Farben, die auf hellem und dunklem Hintergrund lesbar sind (GNOME-Palette)
COL = {
    "cpu_temp": (0.21, 0.52, 0.89),   # blau
    "gpu_temp": (0.90, 0.38, 0.00),   # orange
    "cpu": (0.21, 0.52, 0.89),
    "gpu": (0.90, 0.38, 0.00),
    "mid": (0.15, 0.64, 0.41),        # grün
    "accent": (0.21, 0.52, 0.89),
    "factory": (0.55, 0.55, 0.55),
    "warn": (0.88, 0.11, 0.14),
}

PRESETS = {
    "Leise": ([50, 60, 66, 72, 78, 84, 90, 95], [0, 8, 12, 18, 26, 36, 52, 75]),
    "Ausgewogen": ([45, 58, 65, 70, 76, 82, 88, 93], [5, 12, 20, 28, 38, 50, 68, 90]),
    "Kühl": ([40, 50, 58, 65, 72, 78, 84, 90], [20, 32, 45, 58, 70, 82, 95, 100]),
}


def hexcol(rgb):
    return "#%02x%02x%02x" % tuple(int(c * 255) for c in rgb)


def preset_points(name):
    temps, pcts = PRESETS[name]
    return [[t, hw.pct_to_pwm(p)] for t, p in zip(temps, pcts)]


def systemctl_active(units):
    try:
        r = subprocess.run(["systemctl", "is-active", *units],
                           capture_output=True, text=True, timeout=3)
        states = r.stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        states = []
    return {u: (states[i] if i < len(states) else "unknown") for i, u in enumerate(units)}


def unit_exists(unit):
    try:
        r = subprocess.run(["systemctl", "list-unit-files", unit, "--no-legend"],
                           capture_output=True, text=True, timeout=3)
        return bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


# --------------------------------------------------------------------------- #
# Zeichenhilfen
# --------------------------------------------------------------------------- #

def fg_color(widget):
    c = widget.get_color()
    return c.red, c.green, c.blue


def draw_text(widget, cr, x, y, text, rgb, alpha=1.0, align="left", small=True):
    layout = widget.create_pango_layout(None)
    layout.set_markup(f"<span size='{'small' if small else 'medium'}'>{GLib.markup_escape_text(text)}</span>", -1)
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
    """Zwei übereinanderliegende Verläufe: Temperaturen und Drehzahlen."""

    def __init__(self, history):
        super().__init__()
        self.history = history
        self.set_content_height(260)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def _panel(self, cr, x0, y0, w, h, fg, series, vmin, vmax, unit, step):
        # Gitter + Achse
        cr.set_line_width(1)
        v = vmin
        while v <= vmax + 1e-6:
            y = y0 + h - (v - vmin) / (vmax - vmin) * h
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(x0, y)
            cr.line_to(x0 + w, y)
            cr.stroke()
            label = f"{int(v / 1000)}k" if unit == "RPM" and v else f"{int(v)}"
            draw_text(self, cr, x0 - 6, y, label, fg, 0.6, "right")
            v += step
        draw_text(self, cr, x0, y0 - 14, unit, fg, 0.6)
        now = time.monotonic()
        for key, idx in series:
            pts = [(t, s[idx]) for t, *s in self.history if s[idx] is not None]
            if len(pts) < 2:
                continue
            cr.set_source_rgb(*COL[key])
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
        self._panel(cr, left, top, pw, ph, fg,
                    [("cpu_temp", 0), ("gpu_temp", 1)], 20, 100, "°C", 20)
        rpms = [s[i] for _, *s in self.history for i in (2, 3, 4)
                if s[i] and s[i] <= hw.MAX_PLAUSIBLE_RPM]
        vmax = max(6000, math.ceil(max(rpms, default=0) / 2000) * 2000)
        step = max(2000, math.ceil(vmax / 4 / 1000) * 1000)  # höchstens ~5 Gitterlinien
        self._panel(cr, left, top + ph + gap, pw, ph, fg,
                    [("cpu", 2), ("gpu", 3), ("mid", 4)], 0, vmax, "RPM", step)
        y = height - 6
        draw_text(self, cr, left, y, "−5 min", fg, 0.5)
        draw_text(self, cr, left + pw, y, "jetzt", fg, 0.5, "right")


# --------------------------------------------------------------------------- #
# Kurven-Editor
# --------------------------------------------------------------------------- #

class CurveEditor(Gtk.DrawingArea):
    """8 Punkte per Maus ziehen. x = Temperatur, y = Drehzahl in %."""

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

    # Koordinaten
    def _box(self):
        w, h = self.get_width(), self.get_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def _to_xy(self, t, pwm):
        x0, y0, w, h = self._box()
        return (x0 + (t - self.T0) / (self.T1 - self.T0) * w,
                y0 + h - pwm / hw.PWM_MAX * h)

    def _from_xy(self, x, y):
        x0, y0, w, h = self._box()
        t = self.T0 + (x - x0) / w * (self.T1 - self.T0)
        pwm = (y0 + h - y) / h * hw.PWM_MAX
        return t, pwm

    def clamp_point(self, i, t, pwm):
        pts = self.points
        tmin = pts[i - 1][0] + 1 if i > 0 else self.T0
        tmax = pts[i + 1][0] - 1 if i < len(pts) - 1 else self.T1
        wmin = pts[i - 1][1] if i > 0 else 0
        wmax = pts[i + 1][1] if i < len(pts) - 1 else hw.PWM_MAX
        return int(round(min(max(t, tmin), tmax))), int(round(min(max(pwm, wmin), wmax)))

    def _begin(self, gesture, x, y):
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

    def _update(self, gesture, dx, dy):
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
                x, y = self._to_xy(min(t, self.T1), pwm)
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
            r = 7 if i == self.selected else 5
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.set_source_rgba(*COL["accent"], alpha)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.set_line_width(1.5)
            cr.stroke()


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

        self.saved = hw.load_config()
        self.work = hw.deep_copy(self.saved)
        self.factory = hw.load_factory()
        self.limits_info = hw.read_limits()
        self.fan_labels = {f["fan"]: f["label"] for f in hw.read_fans()}

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        self.apply_btn = Gtk.Button(label="Anwenden")
        self.apply_btn.add_css_class("suggested-action")
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

        cur = hw.read_profile() if hw.read_profile() in hw.PROFILES else hw.PROFILES[0]
        self._updating = True
        self.profile_toggle.set_active_name(cur)
        self._updating = False
        self.curve_profile.set_active_name(cur)
        self.curve_fan.set_active_name(hw.FANS[0])
        self.limit_profile.set_active_name(cur)

        self._init_ppd()
        self._update_dirty()
        self._load_curve_editor()
        self._load_limits()
        self._tick()
        self._refresh_hints()
        GLib.timeout_add_seconds(1, self._tick)
        GLib.timeout_add_seconds(5, self._refresh_hints)

    # ------------------------------------------------------------------ Seiten

    def _page_overview(self):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Verlauf", description="Die letzten 5 Minuten")
        legend = Gtk.Label(xalign=0, use_markup=True, wrap=True)
        legend.set_markup("   ".join(
            f"<span foreground='{hexcol(COL[k])}'>●</span> {t}" for k, t in
            [("cpu_temp", "CPU-Temp."), ("gpu_temp", "GPU-Temp.  |"),
             ("cpu", "CPU-Lüfter"), ("gpu", "GPU-Lüfter"), ("mid", "Mittlerer Lüfter")]))
        legend.add_css_class("caption")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("card")
        box.set_margin_bottom(4)
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
        row = Adw.PreferencesRow(activatable=False)
        self.profile_toggle.set_margin_top(10)
        self.profile_toggle.set_margin_bottom(10)
        self.profile_toggle.set_margin_start(10)
        self.profile_toggle.set_margin_end(10)
        row.set_child(self.profile_toggle)
        g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup(title="Lüfter",
                                 description="Der Schalter setzt einen Lüfter sofort auf Vollgas. "
                                             "Das gilt bis zum Ausschalten oder Neustart.")
        self.fan_rows = {}
        for fan in hw.FANS:
            r = Adw.SwitchRow(title=f"{hw.FAN_NAMES[fan]} · Vollgas")
            r.connect("notify::active", self._on_fullspeed, fan)
            self.fan_rows[fan] = r
            g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(title="Temperaturen")
        self.cpu_temp_row = Adw.ActionRow(title="CPU (Package)")
        self.gpu_temp_row = Adw.ActionRow(title="GPU (NVIDIA)")
        self.cpu_temp_lbl = Gtk.Label(css_classes=["numeric", "title-4"])
        self.gpu_temp_lbl = Gtk.Label(css_classes=["numeric", "title-4"])
        self.cpu_temp_row.add_suffix(self.cpu_temp_lbl)
        self.gpu_temp_row.add_suffix(self.gpu_temp_lbl)
        g.add(self.cpu_temp_row)
        g.add(self.gpu_temp_row)
        page.add(g)

        self.hint_group = Adw.PreferencesGroup(title="Hinweise")
        self.hint_rows = []
        page.add(self.hint_group)
        return page

    def _profile_toggle_group(self):
        tg = Adw.ToggleGroup(homogeneous=True)
        for p in hw.PROFILES:
            tg.add(Adw.Toggle(name=p, label=hw.PROFILE_NAMES[p]))
        return tg

    def _page_curves(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Kurve bearbeiten",
            description="Jedes Profil hat eigene Kurven. Punkte mit der Maus ziehen. "
                        "Die gestrichelte Linie ist die Werkskurve, die rote Linie die aktuelle Temperatur.")
        self.curve_profile = self._profile_toggle_group()
        self.curve_fan = Adw.ToggleGroup(homogeneous=True)
        for fan in hw.FANS:
            self.curve_fan.add(Adw.Toggle(name=fan, label=hw.FAN_NAMES[fan]))
        for tg in (self.curve_profile, self.curve_fan):
            tg.connect("notify::active-name", lambda *_: self._load_curve_editor())
            row = Adw.PreferencesRow(activatable=False)
            tg.set_margin_top(8)
            tg.set_margin_bottom(8)
            tg.set_margin_start(10)
            tg.set_margin_end(10)
            row.set_child(tg)
            g.add(row)
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
        b = Gtk.Button(label="Auf alle Lüfter")
        b.set_tooltip_text("Diese Kurve für alle drei Lüfter dieses Profils übernehmen")
        b.connect("clicked", self._on_copy_all)
        flow.append(b)
        g.add(flow)
        self.curve_status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"])
        self.curve_status.set_margin_top(10)
        g.add(self.curve_status)
        page.add(g)
        return page

    def _page_limits(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Leistungslimits pro Profil",
            description="Weniger Leistung heißt weniger Wärme und leisere Lüfter. "
                        "„Standard“ heißt: Firmware-Standardwert. Die Limits werden bei jedem Profilwechsel gesetzt.")
        self.limit_profile = self._profile_toggle_group()
        self.limit_profile.connect("notify::active-name", lambda *_: self._load_limits())
        row = Adw.PreferencesRow(activatable=False)
        for m in ("top", "bottom"):
            getattr(self.limit_profile, f"set_margin_{m}")(8)
        self.limit_profile.set_margin_start(10)
        self.limit_profile.set_margin_end(10)
        row.set_child(self.limit_profile)
        g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup()
        self.limit_rows = {}
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
        fans = {f["fan"]: f["rpm"] for f in hw.read_fans()}
        cpu = hw.read_cpu_temp()
        gpu_state = hw.gpu_runtime_status(self.gpu_dev)
        if gpu_state == "active":
            if time.monotonic() - self.gpu_temp_time > 3:
                self._poll_gpu()
        else:
            self.gpu_temp = None
        self.history.append((time.monotonic(), cpu, self.gpu_temp,
                             fans.get("cpu"), fans.get("gpu"), fans.get("mid")))
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
        for fan, row in self.fan_rows.items():
            rpm = fans.get(fan)
            row.set_subtitle(f"{rpm} U/min" if rpm is not None else "–")
            if row.get_active() != state[fan]:
                row.set_active(state[fan])
        prof = hw.read_profile()
        if prof in hw.PROFILES and self.profile_toggle.get_active_name() != prof:
            self.profile_toggle.set_active_name(prof)
        self._updating = False

        fan = self.curve_fan.get_active_name()
        self.editor.live_temp = self.gpu_temp if fan == "gpu" else cpu
        self.editor.queue_draw()
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

        if st["asusd.service"] == "active":
            hints.append(("dialog-warning-symbolic", "asusd (asusctl) läuft",
                          "asusd setzt ebenfalls Lüfterkurven. Beide Programme überschreiben sich "
                          "gegenseitig. Verwende nur eines."))
        if st["tuned-ppd.service"] == "active" and st["power-profiles-daemon.service"] == "active":
            hints.append(("dialog-warning-symbolic", "Zwei Profil-Dienste aktiv",
                          "tuned-ppd und power-profiles-daemon laufen gleichzeitig und streiten "
                          "sich um das Energieprofil."))
        elif st["tuned-ppd.service"] == "active":
            hints.append(("emblem-ok-symbolic", "tuned-ppd steuert das Energieprofil",
                          "Wechsel über GNOME oder Fn+F5 werden erkannt. Dabei schaltet die Firmware "
                          "eigene Kurven ab, und der Dienst setzt sie sofort neu."))
        if st["thermald.service"] == "active":
            hints.append(("dialog-information-symbolic", "thermald ist aktiv",
                          "thermald kann die CPU bei Hitze unabhängig von den Lüftern drosseln. "
                          "Das ist ein Schutz und meist sinnvoll, kann aber Leistungslimits unterbieten."))

        prof = hw.read_profile()
        if prof in hw.PROFILES and st["rog-fan-control.service"] == "active":
            pc = self.saved["profiles"][prof]["curves"]
            off = [hw.FAN_NAMES[f] for f in hw.FANS
                   if pc[f]["enabled"] and not (hw.read_curve(f) or {}).get("enabled")]
            if off:
                hints.append(("dialog-warning-symbolic", "Eigene Kurve gerade nicht aktiv",
                              f"Für {', '.join(off)} ist eine eigene Kurve gespeichert, aber die "
                              "Firmware nutzt gerade ihre eigene. Der Dienst setzt sie in wenigen "
                              "Sekunden neu."))
        if any(self.saved["profiles"][p]["limits"].get("nv_dynamic_boost") is not None
               for p in hw.PROFILES) and st["nvidia-powerd.service"] != "active":
            hints.append(("dialog-warning-symbolic", "nvidia-powerd läuft nicht",
                          "Ohne nvidia-powerd hat das Dynamic-Boost-Limit keine Wirkung."))
        fs = [hw.FAN_NAMES[f] for f, on in hw.read_state()["fullspeed"].items() if on]
        if fs:
            hints.append(("dialog-information-symbolic", "Vollgas aktiv",
                          f"{', '.join(fs)}: läuft auf Vollgas. Kurven wirken dort erst nach "
                          "dem Abschalten."))
        if not hints:
            hints.append(("emblem-ok-symbolic", "Keine Konflikte gefunden", ""))

        for icon, title, sub in hints:
            r = Adw.ActionRow(title=title, subtitle=sub, subtitle_lines=0)
            r.add_prefix(Gtk.Image(icon_name=icon))
            self.hint_group.add(r)
            self.hint_rows.append(r)
        return True

    # ------------------------------------------------------------------ Profil

    def _init_ppd(self):
        for name, path in (("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
                           ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")):
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
        if self._ppd:
            proxy, iface = self._ppd
            try:
                proxy.call_sync("org.freedesktop.DBus.Properties.Set",
                                GLib.Variant("(ssv)", (iface, "ActiveProfile",
                                                       GLib.Variant("s", hw.PPD_FOR_PROFILE[prof]))),
                                Gio.DBusCallFlags.NONE, 3000, None)
                self.toast(f"Profil: {hw.PROFILE_NAMES[prof]}")
                return
            except GLib.Error as e:
                self.toast(f"PowerProfiles-Dienst: {e.message}")
        self._run_helper(["set-profile", prof], ok=f"Profil: {hw.PROFILE_NAMES[prof]}")

    # ------------------------------------------------------------------ Vollgas

    def _on_fullspeed(self, row, _pspec, fan):
        if self._updating:
            return
        on = row.get_active()

        def failed():
            self._updating = True
            row.set_active(not on)
            self._updating = False
        self._run_helper(["fullspeed", fan, "on" if on else "off"],
                         ok=f"{hw.FAN_NAMES[fan]}: {'Vollgas' if on else 'Automatik'}",
                         on_fail=failed, after=self._refresh_hints)

    # ------------------------------------------------------------------ Kurven

    def _cur_curve(self):
        p = self.curve_profile.get_active_name() or hw.PROFILES[0]
        f = self.curve_fan.get_active_name() or hw.FANS[0]
        return p, f, self.work["profiles"][p]["curves"][f]

    def _fallback_points(self, p, f):
        fac = (self.factory.get(p) or {}).get(f)
        if fac:
            try:
                return hw.validate_curve(fac)
            except ValueError:
                pass
        return preset_points("Ausgewogen")

    def _load_curve_editor(self):
        if not hasattr(self, "editor"):
            return
        self.factory = hw.load_factory()
        p, f, c = self._cur_curve()
        self._updating = True
        self.curve_enable.set_active(c["enabled"])
        self.curve_enable.set_title(f"Eigene Kurve für {hw.FAN_NAMES[f]} im Profil „{hw.PROFILE_NAMES[p]}“")
        self._updating = False
        if c["points"] is None:
            c["points"] = None  # bleibt leer, bis bearbeitet/aktiviert
            self.editor.points = self._fallback_points(p, f)
        else:
            self.editor.points = c["points"]
        self.editor.factory = (self.factory.get(p) or {}).get(f)
        self.editor.editable = c["enabled"]
        self.factory_btn.set_sensitive(bool(self.editor.factory))
        self.factory_btn.set_tooltip_text(
            None if self.editor.factory else
            "Werkskurve dieses Profils noch unbekannt. Wechsle einmal in das Profil, dann liest der Dienst sie ein.")
        self.editor.selected = self.editor.selected or 0
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
        self.pt_temp.set_subtitle(f"Punkt {i + 1} von {hw.N_POINTS}")
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
            pts = preset_points(name)
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
        for other in hw.FANS:
            self.work["profiles"][p]["curves"][other] = {
                "enabled": c["enabled"], "points": [list(x) for x in self.editor.points]}
        self._update_dirty()
        self.toast(f"Kurve auf alle Lüfter im Profil „{hw.PROFILE_NAMES[p]}“ übertragen")

    def _validate_curve_status(self):
        p, f, c = self._cur_curve()
        if not c["enabled"]:
            self.curve_status.set_label("Die Firmware regelt diesen Lüfter. Aktiviere die eigene Kurve "
                                        "oder wähle eine Vorlage, um zu bearbeiten.")
            return
        try:
            hw.validate_curve(self.editor.points)
            self.curve_status.set_label("Kurve ist gültig. Mit „Anwenden“ speichern.")
        except ValueError as e:
            self.curve_status.set_label(f"⚠ {e}")

    # ------------------------------------------------------------------ Limits

    def _load_limits(self):
        if not hasattr(self, "limit_rows"):
            return
        p = self.limit_profile.get_active_name() or hw.PROFILES[0]
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
        if not os.path.exists(hw.HELPER):
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
