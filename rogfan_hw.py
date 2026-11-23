"""Hardware-Schicht für ASUS-ROG-Laptops (asus-wmi / asus-armoury).

Wird von der GUI (nur lesend), dem pkexec-Helfer und dem Dienst (schreibend)
gemeinsam benutzt. Auf dem G614JI mit Kernel 7.3 gemessenes Verhalten:

* asus_custom_fan_curve/pwmN_enable: 1 = eigene Kurve an, 2 = aus (Firmware),
  3 = aus + Werkskurve des aktiven Profils laden (liest danach 2).
* Der Kernel prüft Kurven NICHT (nicht-monoton, 101 °C wird angenommen),
  deshalb validiert validate_curve() selbst.
* Ein Profilwechsel schaltet eigene Kurven ab -> Dienst setzt sie neu.
* asus-armoury lehnt PL1/PL2-Änderungen ohne aktive eigene Kurve mit EBUSY ab;
  apply_limits() aktiviert dafür kurz die Werkskurve (_ppt_unlock).
* asus/pwmN_enable: 0 = Vollgas, 2 = Automatik. Bei GPU/Mitte liest der Wert
  immer 0, der Zustand wird daher in STATE_PATH mitgeführt. Im Vollgas-Modus
  meldet der mittlere Lüfter die Drehzahl mit gesetztem Bit 15 (_clean_rpm).
"""

import contextlib
import copy
import glob
import json
import os

CONFIG_PATH = "/etc/rog-fan-control/config.json"
FACTORY_PATH = "/var/lib/rog-fan-control/factory.json"
RUN_DIR = "/run/rog-fan-control"
STATE_PATH = RUN_DIR + "/state.json"
LOCK_PATH = RUN_DIR + "/lock"
HELPER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "rog-fan-helper")
SERVICE = "rog-fan-control.service"

PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
PLATFORM_PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"
ARMOURY = "/sys/class/firmware-attributes/asus-armoury/attributes"

PROFILES = ("quiet", "balanced", "performance")
PROFILE_NAMES = {"quiet": "Leise", "balanced": "Ausgewogen", "performance": "Leistung"}
# PowerProfiles-D-Bus (tuned-ppd / power-profiles-daemon) <-> platform_profile
PPD_FOR_PROFILE = {"quiet": "power-saver", "balanced": "balanced", "performance": "performance"}

FANS = ("cpu", "gpu", "mid")  # pwm1, pwm2, pwm3
FAN_NAMES = {"cpu": "CPU-Lüfter", "gpu": "GPU-Lüfter", "mid": "Mittlerer Lüfter"}

LIMITS = {
    "ppt_pl1_spl": ("CPU-Dauerlimit (PL1)", "W"),
    "ppt_pl2_sppt": ("CPU-Kurzzeitlimit (PL2)", "W"),
    "nv_dynamic_boost": ("GPU Dynamic Boost", "W"),
    "nv_temp_target": ("GPU-Temperaturziel", "°C"),
}

N_POINTS = 8
TEMP_MIN, TEMP_MAX = 20, 100
PWM_MAX = 255
MIN_LAST_PWM = 77  # höchster Kurvenpunkt muss mind. 30 % haben

MAX_CONFIG_BYTES = 64 * 1024


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


def fan_index(fan):
    return FANS.index(fan) + 1


def pct_to_pwm(pct):
    return max(0, min(PWM_MAX, round(pct * PWM_MAX / 100)))


def pwm_to_pct(pwm):
    return pwm * 100 / PWM_MAX


# --------------------------------------------------------------------------- #
# Lesen
# --------------------------------------------------------------------------- #

MAX_PLAUSIBLE_RPM = 15000


def _clean_rpm(rpm):
    """Im Vollgas-Modus setzt die Firmware (mittlerer Lüfter, G614JI) Bit 15 im
    Rohwert (Einheit 100 U/min): 3281200 = (0x8000 + 44) * 100 = 4400 U/min."""
    if rpm is None:
        return None
    raw = rpm // 100
    if raw & 0x8000:
        rpm = (raw & 0x7FFF) * 100
    return rpm if 0 <= rpm <= MAX_PLAUSIBLE_RPM else None


def read_fans():
    d = find_hwmon("asus")
    out = []
    for fan in FANS:
        i = fan_index(fan)
        out.append({
            "fan": fan,
            "label": _read(f"{d}/fan{i}_label", fan) if d else fan,
            "rpm": _clean_rpm(_read_int(f"{d}/fan{i}_input")) if d else None,
        })
    return out


def read_curve(fan):
    d = find_hwmon("asus_custom_fan_curve")
    if not d:
        return None
    i = fan_index(fan)
    pts = []
    for p in range(1, N_POINTS + 1):
        t = _read_int(f"{d}/pwm{i}_auto_point{p}_temp")
        w = _read_int(f"{d}/pwm{i}_auto_point{p}_pwm")
        if t is None or w is None:
            return None
        pts.append([t, w])
    return {"points": pts, "enabled": _read(f"{d}/pwm{i}_enable") == "1"}


def read_profile():
    return _read(PLATFORM_PROFILE)


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
    d = find_hwmon("coretemp")
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
    return {"fullspeed": {fan: bool(fs.get(fan)) for fan in FANS}}


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

def default_config():
    return {
        "version": 1,
        "profiles": {
            p: {
                "curves": {fan: {"enabled": False, "points": None} for fan in FANS},
                "limits": {name: None for name in LIMITS},
            }
            for p in PROFILES
        },
    }


def validate_curve(points):
    """Gibt normalisierte Punkte zurück oder wirft ValueError (deutsche Meldung)."""
    if not isinstance(points, list) or len(points) != N_POINTS:
        raise ValueError(f"Eine Kurve braucht genau {N_POINTS} Punkte.")
    out = []
    for n, pt in enumerate(points, 1):
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            raise ValueError(f"Punkt {n} ist ungültig.")
        t, w = pt
        if isinstance(t, bool) or isinstance(w, bool) or not isinstance(t, int) or not isinstance(w, int):
            raise ValueError(f"Punkt {n}: nur ganze Zahlen erlaubt.")
        if not TEMP_MIN <= t <= TEMP_MAX:
            raise ValueError(f"Punkt {n}: Temperatur muss zwischen {TEMP_MIN} und {TEMP_MAX} °C liegen.")
        if not 0 <= w <= PWM_MAX:
            raise ValueError(f"Punkt {n}: Drehzahlwert muss zwischen 0 und {PWM_MAX} liegen.")
        if out:
            if t <= out[-1][0]:
                raise ValueError(f"Punkt {n}: Temperaturen müssen von Punkt zu Punkt steigen.")
            if w < out[-1][1]:
                raise ValueError(f"Punkt {n}: Drehzahl darf bei höherer Temperatur nicht sinken.")
        out.append([t, w])
    if out[-1][1] < MIN_LAST_PWM:
        raise ValueError("Der letzte Punkt muss mindestens 30 % Drehzahl haben.")
    return out


def validate_config(cfg, limits_info=None):
    if not isinstance(cfg, dict):
        raise ValueError("Konfiguration ist kein Objekt.")
    if limits_info is None:
        limits_info = read_limits()
    out = default_config()
    profiles = cfg.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise ValueError("'profiles' ist ungültig.")
    for p in PROFILES:
        src = profiles.get(p) or {}
        curves = src.get("curves") or {}
        for fan in FANS:
            c = curves.get(fan) or {}
            enabled = bool(c.get("enabled"))
            pts = c.get("points")
            if pts is not None:
                try:
                    pts = validate_curve(pts)
                except ValueError as e:
                    raise ValueError(f"{PROFILE_NAMES[p]} / {FAN_NAMES[fan]}: {e}") from None
            if enabled and pts is None:
                raise ValueError(f"{PROFILE_NAMES[p]} / {FAN_NAMES[fan]}: eigene Kurve ohne Punkte.")
            out["profiles"][p]["curves"][fan] = {"enabled": enabled, "points": pts}
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
                    f"{PROFILE_NAMES[p]} / {LIMITS[name][0]}: Wert muss zwischen "
                    f"{info['min']} und {info['max']} liegen.")
            out["profiles"][p]["limits"][name] = v
        lim = out["profiles"][p]["limits"]
        if "ppt_pl1_spl" in limits_info and "ppt_pl2_sppt" in limits_info:
            pl1 = lim["ppt_pl1_spl"] if lim["ppt_pl1_spl"] is not None else limits_info["ppt_pl1_spl"]["default"]
            pl2 = lim["ppt_pl2_sppt"] if lim["ppt_pl2_sppt"] is not None else limits_info["ppt_pl2_sppt"]["default"]
            if pl2 < pl1:
                raise ValueError(f"{PROFILE_NAMES[p]}: PL2 ({pl2} W) darf nicht unter PL1 ({pl1} W) liegen.")
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
    for p in PROFILES:
        for fan in FANS:
            pts = (data.get(p) or {}).get(fan)
            try:
                out.setdefault(p, {})[fan] = validate_curve_loose(pts)
            except ValueError:
                pass
    return out


def validate_curve_loose(points):
    """Für Werkskurven: Form prüfen, aber ohne Sicherheitsregeln."""
    if not isinstance(points, list) or len(points) != N_POINTS:
        raise ValueError("ungültig")
    return [[int(t), int(w)] for t, w in points]


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
    """Lädt die Werkskurven des aktiven Profils (enable=3) und speichert sie."""
    d = find_hwmon("asus_custom_fan_curve")
    if not d or profile not in PROFILES:
        return None
    captured = {}
    for fan in FANS:
        _write(f"{d}/pwm{fan_index(fan)}_enable", 3)
        c = read_curve(fan)
        if c:
            captured[fan] = c["points"]
    data = load_factory()
    data[profile] = captured
    _atomic_write_json(FACTORY_PATH, data)
    return captured


def apply_curves(curves):
    d = find_hwmon("asus_custom_fan_curve")
    if not d:
        return ["Kurven-Schnittstelle (asus_custom_fan_curve) nicht gefunden."]
    errors = []
    for fan in FANS:
        i = fan_index(fan)
        c = curves[fan]
        try:
            if c["enabled"] and c["points"]:
                pts = validate_curve(c["points"])
                for p, (t, w) in enumerate(pts, 1):
                    _write(f"{d}/pwm{i}_auto_point{p}_temp", t)
                    _write(f"{d}/pwm{i}_auto_point{p}_pwm", w)
                _write(f"{d}/pwm{i}_enable", 1)
            elif _read(f"{d}/pwm{i}_enable") == "1":
                _write(f"{d}/pwm{i}_enable", 3)  # zurück zur Werkskurve
        except (OSError, ValueError) as e:
            errors.append(f"Kurve {FAN_NAMES[fan]}: {e}")
    return errors


@contextlib.contextmanager
def _ppt_unlock():
    """Auf dem G614JI lehnt asus-armoury PL1/PL2-Änderungen mit EBUSY ab
    („PPT change requires an active fan curve“), solange keine eigene Kurve
    aktiv ist. Dann wird kurz die Werkskurve des aktiven Profils als eigene
    Kurve aktiviert (gleiches Lüfterverhalten) und danach wieder abgeschaltet.
    Das gesetzte Limit bleibt dabei erhalten (gemessen)."""
    d = find_hwmon("asus_custom_fan_curve")
    unlocked = False
    if d and not any(_read(f"{d}/pwm{i}_enable") == "1" for i in range(1, len(FANS) + 1)):
        try:
            _write(f"{d}/pwm1_enable", 3)  # Werkskurve des aktiven Profils laden
            _write(f"{d}/pwm1_enable", 1)
            unlocked = True
        except OSError:
            pass
    try:
        yield
    finally:
        if unlocked:
            try:
                _write(f"{d}/pwm1_enable", 3)
            except OSError:
                pass


def apply_limits(limits):
    info = read_limits()
    todo = []
    for name, value in limits.items():
        if name not in info:
            continue
        target = value if value is not None else info[name]["default"]
        if target != info[name]["current"]:
            todo.append((name, target))
    if not todo:
        return []
    errors = []
    with _ppt_unlock():
        for name, target in todo:
            try:
                _write(f"{ARMOURY}/{name}/current_value", target)
            except OSError as e:
                errors.append(f"{LIMITS[name][0]}: {e.strerror or e}")
    return errors


def set_fullspeed(fan, on):
    d = find_hwmon("asus")
    if not d:
        raise OSError("ASUS-hwmon nicht gefunden.")
    _write(f"{d}/pwm{fan_index(fan)}_enable", 0 if on else 2)
    st = read_state()
    st["fullspeed"][fan] = bool(on)
    _atomic_write_json(STATE_PATH, st)


def reassert_fullspeed():
    errors = []
    d = find_hwmon("asus")
    for fan, on in read_state()["fullspeed"].items():
        if on and d:
            try:
                _write(f"{d}/pwm{fan_index(fan)}_enable", 0)
            except OSError as e:
                errors.append(f"Vollgas {FAN_NAMES[fan]}: {e}")
    return errors


def apply_profile(cfg, profile):
    """Setzt Kurven + Limits eines Profils. Gibt eine Fehlerliste zurück."""
    if profile not in PROFILES:
        return [f"Unbekanntes Profil: {profile}"]
    pc = cfg["profiles"][profile]
    errors = apply_curves(pc["curves"])
    errors += apply_limits(pc["limits"])
    errors += reassert_fullspeed()
    return errors


def curves_match(cfg, profile):
    """True, wenn die Hardware dem gewünschten Kurvenzustand entspricht."""
    for fan in FANS:
        want = cfg["profiles"][profile]["curves"][fan]
        have = read_curve(fan)
        if have is None:
            return True  # nichts zu tun
        if want["enabled"] and want["points"]:
            if not have["enabled"] or have["points"] != want["points"]:
                return False
        elif have["enabled"]:
            return False
    return True


def deep_copy(obj):
    return copy.deepcopy(obj)
