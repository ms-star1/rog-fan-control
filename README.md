# ROG Fan Control (ROG Strix G614JI, Fedora)

GTK4/libadwaita-App für Lüfterkurven, Vollgas, Energieprofile und Leistungslimits.

| Teil | Ort | Aufgabe |
|---|---|---|
| Oberfläche | `rog-fan-control` (App-Menü: „ROG Lüftersteuerung") | läuft als Benutzer, liest nur |
| Helfer | `<libexec>/rog-fan-control/rog-fan-helper` | schreibt geprüfte Werte, via pkexec |
| Dienst | `rog-fan-control.service` | setzt Kurven/Limits bei Boot, Profilwechsel, nach Standby |
| Konfiguration | `/etc/rog-fan-control/config.json` | eigene Kurven + Limits pro Profil |
| Werkskurven | `/var/lib/rog-fan-control/factory.json` | vom Dienst beim Profilwechsel eingelesen |

## Installieren

**Als RPM** (empfohlen): Das RPM aus dem neuesten GitHub-Release oder aus `make rpm` installieren:

```sh
sudo dnf install ./rog-fan-control-*.noarch.rpm
sudo systemctl enable --now rog-fan-control   # Fedora aktiviert neue Dienste nicht automatisch
```

**Ohne RPM** nach `/usr/local`: `sudo ./install.sh`, Entfernen mit `sudo ./uninstall.sh [--purge]`.
Nicht beides gleichzeitig installieren, sonst laufen zwei Dienste.

Log: `journalctl -u rog-fan-control`

## Bauen

```sh
make check   # Syntax, .desktop, Polkit-XML
make rpm     # -> build/rpm/RPMS/noarch/*.rpm
```

GitHub Actions baut bei jedem Push auf `main` das RPM (Artefakt „rpms“). Ein Tag `vX.Y.Z`,
der zur `Version:` in `rog-fan-control.spec` passt, erstellt ein Release mit den RPMs.

Gemessenes Kernel-Verhalten (7.3): `asus_custom_fan_curve/pwmN_enable` 1 = eigene Kurve,
2 = Firmware, 3 = Firmware + Werkskurve laden. Der Kernel prüft Kurven nicht, deshalb
`validate_curve()`. Ein Profilwechsel schaltet eigene Kurven ab. `asus/pwmN_enable` 0 = Vollgas,
2 = Auto (GPU/Mitte lesen immer 0 → Zustand in `/run/rog-fan-control/state.json`).
