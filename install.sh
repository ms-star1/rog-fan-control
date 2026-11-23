#!/bin/bash
# Installiert ROG Fan Control nach /usr/local (ohne RPM). Aufruf: sudo ./install.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
cd "$(dirname "$(readlink -f "$0")")"

# Bauen als normaler Benutzer, damit build/ nicht root gehört
if [ -n "${SUDO_USER:-}" ]; then sudo -u "$SUDO_USER" make all; else make all; fi
make install PREFIX=/usr/local

command -v restorecon >/dev/null && restorecon -R /usr/local/libexec/rog-fan-control \
    /usr/local/bin/rog-fan-control /etc/systemd/system/rog-fan-control.service \
    /usr/share/polkit-1/actions/org.local.rogfancontrol.policy \
    /usr/local/share/applications /usr/local/share/icons/hicolor /etc/rog-fan-control || true
gtk-update-icon-cache -q -t /usr/local/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/local/share/applications 2>/dev/null || true

systemctl daemon-reload
systemctl enable rog-fan-control.service
systemctl restart rog-fan-control.service
echo "Installiert. Starten über das App-Menü (ROG Lüftersteuerung) oder: rog-fan-control"
