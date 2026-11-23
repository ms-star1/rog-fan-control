#!/bin/bash
# Entfernt ROG Fan Control und gibt Lüfter und Limits an die Firmware zurück.
# Aufruf: sudo ./uninstall.sh  [--purge entfernt auch die gespeicherte Konfiguration]
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }

systemctl disable --now rog-fan-control.service 2>/dev/null || true

# Firmware-Steuerung wiederherstellen: Werkskurven + Automatik
for h in /sys/class/hwmon/hwmon*; do
    case "$(cat "$h/name" 2>/dev/null)" in
        asus_custom_fan_curve) for i in 1 2 3; do echo 3 > "$h/pwm${i}_enable" 2>/dev/null || true; done ;;
        asus) for i in 1 2 3; do echo 2 > "$h/pwm${i}_enable" 2>/dev/null || true; done ;;
    esac
done

A=/sys/class/firmware-attributes/asus-armoury/attributes
for n in ppt_pl1_spl ppt_pl2_sppt nv_dynamic_boost nv_temp_target; do
    [ -f "$A/$n/default_value" ] && cat "$A/$n/default_value" > "$A/$n/current_value" 2>/dev/null || true
done

rm -rf /usr/local/libexec/rog-fan-control /run/rog-fan-control
rm -f /usr/local/bin/rog-fan-control /etc/systemd/system/rog-fan-control.service \
      /usr/share/polkit-1/actions/org.local.rogfancontrol.policy \
      /usr/local/share/applications/org.local.RogFanControl.desktop \
      /usr/local/share/icons/hicolor/scalable/apps/org.local.RogFanControl.svg
[ "${1:-}" = "--purge" ] && rm -rf /etc/rog-fan-control /var/lib/rog-fan-control
systemctl daemon-reload
echo "Entfernt. Lüfter und Leistungslimits stehen wieder auf Werkseinstellung."
