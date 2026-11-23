"""Hardware-Schicht für ASUS-ROG-Laptops (asus-wmi / asus-armoury).

Wird von der GUI (nur lesend), dem pkexec-Helfer und dem Dienst (schreibend)
gemeinsam benutzt. Lüfter, Kurvenpunkte, Profile und Limits werden zur Laufzeit
erkannt, damit kompatible Modelle mit anderer Ausstattung ebenfalls funktionieren.

Auf dem Testgerät (ROG Strix G16 G614JI, Kernel 7.3) gemessenes Verhalten:

* asus_custom_fan_curve/pwmN_enable: 1 = eigene Kurve an, 2 = aus (Firmware),
  3 = aus + Werkskurve des aktiven Profils laden (liest danach 2).
* Der Kernel prüft Kurven NICHT (nicht-monoton, 101 °C wird angenommen),
  deshalb validiert validate_curve() selbst.
* Ein Profilwechsel schaltet eigene Kurven ab -> Dienst setzt sie neu.
* asus-armoury lehnt PL1/PL2-Änderungen ohne aktive eigene Kurve mit EBUSY ab;
  apply_limits() aktiviert dann kurz die Werkskurve (_ppt_unlock).
* asus/pwmN_enable: 0 = Vollgas, 2 = Automatik. Bei GPU/Mitte liest der Wert
  immer 0, der Zustand wird daher in STATE_PATH mitgeführt. Im Vollgas-Modus
  meldet der mittlere Lüfter die Drehzahl mit gesetztem Bit 15 (_clean_rpm).
"""

import contextlib
import copy
import errno
import functools
import glob
import json
import os
import platform
import subprocess
import time

APP_VERSION = "0.2.0"
REPO_URL = "https://github.com/ms-star1/rog-fan-control"
TESTED_DEVICE = "ASUS ROG Strix G16 G614JI (BIOS G614JI.333), Fedora 45, Kernel 7.3.4"

CONFIG_PATH = "/etc/rog-fan-control/config.json"
STATE_DIR = "/var/lib/rog-fan-control"
FACTORY_PATH = STATE_DIR + "/factory.json"
DEVICE_PATH = STATE_DIR + "/device.json"
RUN_DIR = "/run/rog-fan-control"
STATE_PATH = RUN_DIR + "/state.json"
LOCK_PATH = RUN_DIR + "/lock"
HELPER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "rog-fan-helper")
SERVICE = "rog-fan-control.service"

PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"
ARMOURY = "/sys/class/firmware-attributes/asus-armoury/attributes"
DMI = "/sys/class/dmi/id"

# Reihenfolge und Namen bekannter platform_profile-Werte
PROFILE_ORDER = ("low-power", "quiet", "cool", "balanced", "balanced-performance", "performance")
PROFILE_NAMES = {
    "low-power": "Stromsparen", "quiet": "Leise", "cool": "Kühl", "balanced": "Ausgewogen",
    "balanced-performance": "Ausgewogen+", "performance": "Leistung", "default": "Standard",
}
# platform_profile -> PowerProfiles-D-Bus (tuned-ppd / power-profiles-daemon)
PPD_FOR_PROFILE = {
    "low-power": "power-saver", "quiet": "power-saver", "cool": "power-saver",
    "balanced": "balanced", "balanced-performance": "performance", "performance": "performance",
}
PPD_BUS = ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles")

KNOWN_FAN_LABELS = {"cpu_fan": "cpu", "gpu_fan": "gpu", "mid_fan": "mid", "sys_fan": "sys"}
KNOWN_FAN_NAMES = {"cpu": "CPU-Lüfter", "gpu": "GPU-Lüfter", "mid": "Mittlerer Lüfter",
                   "sys": "System-Lüfter"}

LIMITS = {
    "ppt_pl1_spl": ("CPU-Dauerlimit (PL1)", "W"),
    "ppt_pl2_sppt": ("CPU-Kurzzeitlimit (PL2)", "W"),
    "ppt_fppt": ("CPU-Spitzenlimit (FPPT)", "W"),
    "ppt_pl3_fppt": ("CPU-Spitzenlimit (PL3)", "W"),
    "ppt_apu_sppt": ("APU-Kurzzeitlimit", "W"),
    "ppt_platform_sppt": ("Plattform-Kurzzeitlimit", "W"),
    "nv_dynamic_boost": ("GPU Dynamic Boost", "W"),
    "nv_temp_target": ("GPU-Temperaturziel", "°C"),
}
PPT_LIMITS = tuple(n for n in LIMITS if n.startswith("ppt_"))

TEMP_MIN, TEMP_MAX = 20, 100
PWM_MAX = 255
MIN_LAST_PWM = 77  # höchster Kurvenpunkt muss mind. 30 % haben
MAX_PLAUSIBLE_RPM = 15000
MAX_CONFIG_BYTES = 64 * 1024
VERDICT_NAMES = {"full": "voll kompatibel", "limited": "eingeschränkt kompatibel",
                 "incompatible": "nicht kompatibel"}


# --------------------------------------------------------------------------- #
# Low-level
# --------------------------------------------------------------------------- #

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _read_int(path, default=None):
    v = _read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def find_hwmon(name):
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _read(os.path.join(d, "name")) == name:
            return d
    return None


def pct_to_pwm(pct):
    return max(0, min(PWM_MAX, round(pct * PWM_MAX / 100)))


def pwm_to_pct(pwm):
    return pwm * 100 / PWM_MAX


def _indices(d, pattern, prefix):
    out = set()
    if d:
        for p in glob.glob(os.path.join(d, pattern)):
            n = os.path.basename(p)[len(prefix):].split("_", 1)[0]
            if n.isdigit():
                out.add(int(n))
    return out


# --------------------------------------------------------------------------- #
# Geräteerkennung
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def detect_fans():
    """Erkennt Lüfter aus hwmon 'asus' und 'asus_custom_fan_curve'.

    Gibt eine Liste von Dicts zurück: key, index, label, has_rpm,
    curve_points (0 = keine Kurve), fullspeed (Modus-Datei vorhanden)."""
    a = find_hwmon("asus")
    c = find_hwmon("asus_custom_fan_curve")
    idx = _indices(a, "fan*_input", "fan") | _indices(c, "pwm*_auto_point1_temp", "pwm")
    fans, used = [], set()
    for i in sorted(idx):
        label = _read(f"{a}/fan{i}_label", "") if a else ""
        key = KNOWN_FAN_LABELS.get(label) or f"fan{i}"
        if key in used:
            key = f"fan{i}"
        used.add(key)
        npts = 0
        if c:
            while os.path.exists(f"{c}/pwm{i}_auto_point{npts + 1}_temp"):
                npts += 1
        fans.append({
            "key": key, "index": i, "label": label,
            "has_rpm": bool(a) and os.path.exists(f"{a}/fan{i}_input"),
            "curve_points": npts,
            "fullspeed": bool(a) and os.path.exists(f"{a}/pwm{i}_enable"),
        })
    return fans


def fan_keys():
    return [f["key"] for f in detect_fans()]


def curve_fans():
    return [f["key"] for f in detect_fans() if f["curve_points"] >= 2]


def fan_info(key):
    for f in detect_fans():
        if f["key"] == key:
            return f
    raise KeyError(key)


def fan_name(key):
    if key in KNOWN_FAN_NAMES:
        return KNOWN_FAN_NAMES[key]
    try:
        f = fan_info(key)
        return f"Lüfter {f['index']}" + (f" ({f['label']})" if f["label"] else "")
    except KeyError:
        return key


def n_points(key):
    return fan_info(key)["curve_points"]


def profiles():
    """Verfügbare Profile in sinnvoller Reihenfolge (leer -> ['default'])."""
    choices = read_profile_choices()
    if not choices:
        return ["default"]
    known = [p for p in PROFILE_ORDER if p in choices]
    return known + [p for p in choices if p not in known and p != "custom"]


def profile_name(p):
    return PROFILE_NAMES.get(p, p)


def ppd_mapping_unique():
    """True, wenn jedes Profil genau einem PowerProfiles-Modus entspricht."""
    mapped = [PPD_FOR_PROFILE.get(p) for p in profiles()]
    return None not in mapped and len(set(mapped)) == len(mapped)


def read_dmi():
    keys = ("sys_vendor", "product_name", "product_family", "board_name", "bios_version", "bios_date")
    return {k: _read(f"{DMI}/{k}", "") for k in keys}


# --------------------------------------------------------------------------- #
# Lesen
# --------------------------------------------------------------------------- #

def _clean_rpm(rpm):
    """Im Vollgas-Modus setzt die Firmware (mittlerer Lüfter, G614JI) Bit 15 im
    Rohwert (Einheit 100 U/min): 3281200 = (0x8000 + 44) * 100 = 4400 U/min."""
    if rpm is None:
        return None
    raw = rpm // 100
    if raw & 0x8000:
        rpm = (raw & 0x7FFF) * 100
    return rpm if 0 <= rpm <= MAX_PLAUSIBLE_RPM else None


def read_fan_raw(key):
    d = find_hwmon("asus")
    return _read_int(f"{d}/fan{fan_info(key)['index']}_input") if d else None


def read_fans():
    d = find_hwmon("asus")
    out = []
    for f in detect_fans():
        i = f["index"]
        out.append({
            "fan": f["key"],
            "label": f["label"],
            "rpm": _clean_rpm(_read_int(f"{d}/fan{i}_input")) if d and f["has_rpm"] else None,
        })
    return out


def read_curve(key):
    d = find_hwmon("asus_custom_fan_curve")
    f = fan_info(key)
    if not d or f["curve_points"] < 2:
        return None
    i = f["index"]
    pts = []
    for p in range(1, f["curve_points"] + 1):
        t = _read_int(f"{d}/pwm{i}_auto_point{p}_temp")
        w = _read_int(f"{d}/pwm{i}_auto_point{p}_pwm")
        if t is None or w is None:
            return None
        pts.append([t, w])
    return {"points": pts, "enabled": _read(f"{d}/pwm{i}_enable") == "1"}


def read_profile():
    p = _read(PLATFORM_PROFILE)
    return p if p is not None else "default"


def read_profile_choices():
    return (_read(PLATFORM_PROFILE_CHOICES) or "").split()


def read_limits():
    out = {}
    for name in LIMITS:
        base = f"{ARMOURY}/{name}"
        cur = _read_int(f"{base}/current_value")
        if cur is None:
            continue
        out[name] = {
            "current": cur,
            "default": _read_int(f"{base}/default_value", cur),
            "min": _read_int(f"{base}/min_value", cur),
            "max": _read_int(f"{base}/max_value", cur),
        }
    return out


def read_cpu_temp():
    d = find_hwmon("coretemp") or find_hwmon("k10temp")
    if d:
        v = _read_int(f"{d}/temp1_input")
        if v is not None:
            return v / 1000
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        if _read(f"{z}/type") in ("TCPU", "x86_pkg_temp"):
            v = _read_int(f"{z}/temp")
            if v is not None:
                return v / 1000
    return None


def find_nvidia_gpu():
    for d in glob.glob("/sys/bus/pci/devices/*"):
        if _read(f"{d}/vendor") == "0x10de" and (_read(f"{d}/class") or "").startswith("0x03"):
            return d
    return None


def gpu_runtime_status(dev):
    return _read(f"{dev}/power/runtime_status") if dev else None


def read_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    fs = st.get("fullspeed") if isinstance(st.get("fullspeed"), dict) else {}
    return {"fullspeed": {k: bool(fs.get(k)) for k in fan_keys()}}


def load_device():
    try:
        with open(DEVICE_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def max_rpm(key, device=None):
    device = device if device is not None else load_device()
    try:
        v = device["fans"][key]["max_rpm"]
        return v if isinstance(v, int) and v > 0 else None
    except (TypeError, KeyError):
        return None


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

def default_config():
    return {
        "version": 1,
        "profiles": {
            p: {
                "curves": {k: {"enabled": False, "points": None} for k in curve_fans()},
                "limits": {name: None for name in LIMITS},
            }
            for p in profiles()
        },
    }


def validate_curve(points, n=None):
    """Gibt normalisierte Punkte zurück oder wirft ValueError (deutsche Meldung)."""
    if not isinstance(points, list) or len(points) < 2 or (n is not None and len(points) != n):
        raise ValueError(f"Eine Kurve braucht genau {n} Punkte." if n else "Kurve ist ungültig.")
    out = []
    for i, pt in enumerate(points, 1):
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            raise ValueError(f"Punkt {i} ist ungültig.")
        t, w = pt
        if isinstance(t, bool) or isinstance(w, bool) or not isinstance(t, int) or not isinstance(w, int):
            raise ValueError(f"Punkt {i}: nur ganze Zahlen erlaubt.")
        if not TEMP_MIN <= t <= TEMP_MAX:
            raise ValueError(f"Punkt {i}: Temperatur muss zwischen {TEMP_MIN} und {TEMP_MAX} °C liegen.")
        if not 0 <= w <= PWM_MAX:
            raise ValueError(f"Punkt {i}: Drehzahlwert muss zwischen 0 und {PWM_MAX} liegen.")
        if out:
            if t <= out[-1][0]:
                raise ValueError(f"Punkt {i}: Temperaturen müssen von Punkt zu Punkt steigen.")
            if w < out[-1][1]:
                raise ValueError(f"Punkt {i}: Drehzahl darf bei höherer Temperatur nicht sinken.")
        out.append([t, w])
    if out[-1][1] < MIN_LAST_PWM:
        raise ValueError("Der letzte Punkt muss mindestens 30 % Drehzahl haben.")
    return out


def resample_curve(temps, pcts, n):
    """Vorlage (beliebig viele Stützstellen, Temp/%) auf n Punkte umrechnen."""
    if n == len(temps):
        return [[t, pct_to_pwm(p)] for t, p in zip(temps, pcts)]
    t0, t1 = temps[0], temps[-1]
    out = []
    for i in range(n):
        t = round(t0 + (t1 - t0) * i / (n - 1)) if n > 1 else t1
        if out and t <= out[-1][0]:
            t = out[-1][0] + 1
        for j in range(len(temps) - 1):
            if temps[j] <= t <= temps[j + 1]:
                f = (t - temps[j]) / (temps[j + 1] - temps[j])
                pct = pcts[j] + f * (pcts[j + 1] - pcts[j])
                break
        else:
            pct = pcts[-1]
        w = pct_to_pwm(pct)
        if out and w < out[-1][1]:
            w = out[-1][1]
        out.append([min(t, TEMP_MAX), w])
    return out


def validate_config(cfg, limits_info=None):
    if not isinstance(cfg, dict):
        raise ValueError("Konfiguration ist kein Objekt.")
    if limits_info is None:
        limits_info = read_limits()
    out = default_config()
    src_profiles = cfg.get("profiles") or {}
    if not isinstance(src_profiles, dict):
        raise ValueError("'profiles' ist ungültig.")
    for p in profiles():
        src = src_profiles.get(p) or {}
        curves = src.get("curves") or {}
        for key in curve_fans():
            c = curves.get(key) or {}
            enabled = bool(c.get("enabled"))
            pts = c.get("points")
            if pts is not None:
                try:
                    pts = validate_curve(pts, n_points(key))
                except ValueError as e:
                    if not enabled:
                        pts = None  # z. B. Punktzahl passt nicht mehr: verwerfen
                    else:
                        raise ValueError(f"{profile_name(p)} / {fan_name(key)}: {e}") from None
            if enabled and pts is None:
                raise ValueError(f"{profile_name(p)} / {fan_name(key)}: eigene Kurve ohne Punkte.")
            out["profiles"][p]["curves"][key] = {"enabled": enabled, "points": pts}
        limits = src.get("limits") or {}
        for name in LIMITS:
            v = limits.get(name)
            if v is None:
                continue
            info = limits_info.get(name)
            if info is None:
                continue  # Attribut fehlt auf diesem Gerät
            if isinstance(v, bool) or not isinstance(v, int) or not info["min"] <= v <= info["max"]:
                raise ValueError(
                    f"{profile_name(p)} / {LIMITS[name][0]}: Wert muss zwischen "
                    f"{info['min']} und {info['max']} liegen.")
            out["profiles"][p]["limits"][name] = v
        lim = out["profiles"][p]["limits"]
        if "ppt_pl1_spl" in limits_info and "ppt_pl2_sppt" in limits_info:
            pl1 = lim["ppt_pl1_spl"] if lim["ppt_pl1_spl"] is not None else limits_info["ppt_pl1_spl"]["default"]
            pl2 = lim["ppt_pl2_sppt"] if lim["ppt_pl2_sppt"] is not None else limits_info["ppt_pl2_sppt"]["default"]
            if pl2 < pl1:
                raise ValueError(f"{profile_name(p)}: PL2 ({pl2} W) darf nicht unter PL1 ({pl1} W) liegen.")
    return out


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
        return validate_config(raw)
    except (OSError, ValueError):
        return default_config()


def _atomic_write_json(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def save_config(cfg):
    _atomic_write_json(CONFIG_PATH, cfg)


def load_factory():
    try:
        with open(FACTORY_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for p in profiles():
        for key in curve_fans():
            pts = (data.get(p) or {}).get(key)
            if isinstance(pts, list) and len(pts) == n_points(key):
                try:
                    out.setdefault(p, {})[key] = [[int(t), int(w)] for t, w in pts]
                except (TypeError, ValueError):
                    pass
    return out


# --------------------------------------------------------------------------- #
# Schreiben (nur root)
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def hw_lock():
    import fcntl
    os.makedirs(RUN_DIR, mode=0o755, exist_ok=True)
    with open(LOCK_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def capture_factory(profile):
    """Lädt die Werkskurven des aktiven Profils (enable=3) und speichert sie.

    Speichert nur, wenn `profile` beim Einlesen wirklich aktiv ist – sonst
    landen nach schnellen Profilwechseln falsche Kurven unter dem Namen."""
    d = find_hwmon("asus_custom_fan_curve")
    if not d or not curve_fans() or read_profile() != profile:
        return None
    captured = {}
    for key in curve_fans():
        _write(f"{d}/pwm{fan_info(key)['index']}_enable", 3)
        c = read_curve(key)
        if c:
            captured[key] = c["points"]
    try:
        with open(FACTORY_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[profile] = captured
    _atomic_write_json(FACTORY_PATH, data)
    return captured


def apply_curves(curves):
    d = find_hwmon("asus_custom_fan_curve")
    if not d:
        return [] if not curve_fans() else ["Kurven-Schnittstelle (asus_custom_fan_curve) nicht gefunden."]
    errors = []
    for key in curve_fans():
        i = fan_info(key)["index"]
        c = curves.get(key) or {"enabled": False, "points": None}
        try:
            if c["enabled"] and c["points"]:
                pts = validate_curve(c["points"], n_points(key))
                for p, (t, w) in enumerate(pts, 1):
                    _write(f"{d}/pwm{i}_auto_point{p}_temp", t)
                    _write(f"{d}/pwm{i}_auto_point{p}_pwm", w)
                _write(f"{d}/pwm{i}_enable", 1)
            elif _read(f"{d}/pwm{i}_enable") == "1":
                _write(f"{d}/pwm{i}_enable", 3)  # zurück zur Werkskurve
        except (OSError, ValueError) as e:
            errors.append(f"Kurve {fan_name(key)}: {e}")
    return errors


@contextlib.contextmanager
def _ppt_unlock():
    """Manche Modelle (z. B. G614JI) lehnen PL-Änderungen mit EBUSY ab
    („PPT change requires an active fan curve“), solange keine eigene Kurve
    aktiv ist. Dann wird kurz die Werkskurve des aktiven Profils als eigene
    Kurve aktiviert (gleiches Lüfterverhalten) und danach wieder abgeschaltet.
    Das gesetzte Limit bleibt dabei erhalten (gemessen auf dem G614JI)."""
    d = find_hwmon("asus_custom_fan_curve")
    keys = curve_fans()
    unlocked = None
    if d and keys and not any(_read(f"{d}/pwm{fan_info(k)['index']}_enable") == "1" for k in keys):
        i = fan_info(keys[0])["index"]
        try:
            _write(f"{d}/pwm{i}_enable", 3)  # Werkskurve des aktiven Profils laden
            _write(f"{d}/pwm{i}_enable", 1)
            unlocked = i
        except OSError:
            pass
    try:
        yield unlocked is not None
    finally:
        if unlocked is not None:
            try:
                _write(f"{d}/pwm{unlocked}_enable", 3)
            except OSError:
                pass


def write_limit(name, value):
    """Schreibt ein Limit; bei EBUSY einmal mit _ppt_unlock() wiederholen."""
    path = f"{ARMOURY}/{name}/current_value"
    try:
        _write(path, value)
        return False
    except OSError as e:
        if e.errno != errno.EBUSY:
            raise
    with _ppt_unlock() as ok:
        if not ok:
            raise OSError(errno.EBUSY, "Gerät belegt (Firmware verlangt eine aktive eigene Lüfterkurve)")
        _write(path, value)
    return True


def apply_limits(limits):
    info = read_limits()
    errors = []
    for name, value in limits.items():
        if name not in info:
            continue
        target = value if value is not None else info[name]["default"]
        if target == info[name]["current"]:
            continue
        try:
            write_limit(name, target)
        except OSError as e:
            errors.append(f"{LIMITS[name][0]}: {e.strerror or e}")
    return errors


def _fan_mode_path(key):
    d = find_hwmon("asus")
    f = fan_info(key)
    if not d or not f["fullspeed"]:
        raise OSError(f"{fan_name(key)}: Vollgas wird nicht unterstützt.")
    return f"{d}/pwm{f['index']}_enable"


def set_fullspeed(key, on, remember=True):
    _write(_fan_mode_path(key), 0 if on else 2)
    if remember:
        st = read_state()
        st["fullspeed"][key] = bool(on)
        _atomic_write_json(STATE_PATH, st)


def reassert_fullspeed():
    errors = []
    for key, on in read_state()["fullspeed"].items():
        if on:
            try:
                _write(_fan_mode_path(key), 0)
            except OSError as e:
                errors.append(f"Vollgas {fan_name(key)}: {e}")
    return errors


def apply_profile(cfg, profile):
    """Setzt Kurven + Limits eines Profils. Gibt eine Fehlerliste zurück."""
    if profile not in cfg["profiles"]:
        return [f"Unbekanntes Profil: {profile}"]
    pc = cfg["profiles"][profile]
    errors = apply_curves(pc["curves"])
    errors += apply_limits(pc["limits"])
    errors += reassert_fullspeed()
    return errors


def curves_match(cfg, profile):
    """True, wenn die Hardware dem gewünschten Kurvenzustand entspricht."""
    if profile not in cfg["profiles"]:
        return True
    for key in curve_fans():
        want = cfg["profiles"][profile]["curves"].get(key) or {}
        have = read_curve(key)
        if have is None:
            continue
        if want.get("enabled") and want.get("points"):
            if not have["enabled"] or have["points"] != want["points"]:
                return False
        elif have["enabled"]:
            return False
    return True


def switch_profile(profile, timeout=8.0):
    """Wechselt das Profil (bevorzugt über PowerProfiles, damit tuned-ppd/ppd
    mitzieht) und wartet, bis platform_profile stabil diesen Wert hat."""
    if profile == "default" or profile not in read_profile_choices():
        return read_profile() == profile
    via_ppd = False
    if ppd_mapping_unique() and profile in PPD_FOR_PROFILE:
        try:
            r = subprocess.run(["busctl", "--system", "set-property", PPD_BUS[0], PPD_BUS[1], PPD_BUS[0],
                                "ActiveProfile", "s", PPD_FOR_PROFILE[profile]],
                               capture_output=True, text=True, timeout=5)
            via_ppd = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            via_ppd = False
    if not via_ppd:
        _write(PLATFORM_PROFILE, profile)
    end = time.monotonic() + timeout
    stable_since = None
    while time.monotonic() < end:
        if read_profile() == profile:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 1.5:
                return True
        else:
            stable_since = None
        time.sleep(0.25)
    return read_profile() == profile


# --------------------------------------------------------------------------- #
# Einrichtung / Gerätebericht
# --------------------------------------------------------------------------- #

def compatibility():
    """Liest ohne Schreibzugriffe, was dieses Gerät kann."""
    fans = detect_fans()
    limits = read_limits()
    has_hwmon = find_hwmon("asus") is not None
    has_curves = any(f["curve_points"] >= 2 for f in fans)
    if not has_hwmon and not has_curves:
        verdict = "incompatible"
    elif has_curves and len(profiles()) > 1:
        verdict = "full"
    else:
        verdict = "limited"
    return {
        "verdict": verdict,
        "dmi": read_dmi(),
        "kernel": platform.release(),
        "fans": fans,
        "profiles": profiles(),
        "limits": limits,
        "asus_hwmon": has_hwmon,
        "curve_hwmon": find_hwmon("asus_custom_fan_curve") is not None,
        "armoury": os.path.isdir(ARMOURY),
        "nvidia": find_nvidia_gpu() is not None,
    }


def device_report(device=None):
    """Markdown-Bericht (für Issues), aus device.json oder Live-Erkennung."""
    comp = compatibility()
    device = device if device is not None else (load_device() or {})
    dmi = comp["dmi"]
    lines = [
        "### ROG Fan Control – Gerätebericht",
        "",
        f"- Gerät: {dmi['sys_vendor']} {dmi['product_name']} (Board {dmi['board_name']})",
        f"- BIOS: {dmi['bios_version']} ({dmi['bios_date']})",
        f"- Kernel: {comp['kernel']}",
        f"- Ergebnis: {VERDICT_NAMES[comp['verdict']]}",
        f"- Profile: {', '.join(comp['profiles'])}",
        f"- ROG Fan Control: {APP_VERSION}",
        f"- Schnittstellen: asus-hwmon={'ja' if comp['asus_hwmon'] else 'nein'}, "
        f"Kurven={'ja' if comp['curve_hwmon'] else 'nein'}, asus-armoury={'ja' if comp['armoury'] else 'nein'}, "
        f"NVIDIA={'ja' if comp['nvidia'] else 'nein'}",
        "",
        "| Lüfter | Index | Label | Kurvenpunkte | Vollgas | Höchstdrehzahl |",
        "|---|---|---|---|---|---|",
    ]
    dfans = device.get("fans") or {}
    for f in comp["fans"]:
        d = dfans.get(f["key"]) or {}
        fs = "ja" if f["fullspeed"] else "nein"
        if d.get("fullspeed_effective") is False:
            fs = "Datei da, keine Wirkung"
        mr = f"{d['max_rpm']} U/min" if d.get("max_rpm") else "nicht gemessen"
        lines.append(f"| {fan_name(f['key'])} | {f['index']} | {f['label'] or '–'} | "
                     f"{f['curve_points'] or '–'} | {fs} | {mr} |")
    lines += ["", "| Limit | aktuell | Standard | Bereich |", "|---|---|---|---|"]
    for n, v in comp["limits"].items():
        lines.append(f"| {LIMITS[n][0]} | {v['current']} | {v['default']} | {v['min']}–{v['max']} |")
    q = device.get("quirks") or {}
    lines += ["", "Eigenheiten:"]
    ppt = q.get("ppt_needs_curve")
    lines.append("- PL-Änderung braucht aktive Kurve: " +
                 {True: "ja (wird automatisch umgangen)", False: "nein", None: "nicht geprüft"}[ppt])
    lines.append("- Drehzahl mit Statusbit 15 bei Vollgas: " +
                 (", ".join(fan_name(k) for k in q.get("rpm_bit15_fans", [])) or "nicht beobachtet"))
    if device.get("factory_profiles"):
        lines.append(f"- Werkskurven eingelesen für: {', '.join(device['factory_profiles'])}")
    if device.get("created"):
        lines.append(f"- Eingerichtet: {device['created']}")
    return "\n".join(lines) + "\n"


def deep_copy(obj):
    return copy.deepcopy(obj)
