# ROG Fan Control

GTK4/libadwaita-App für Linux zur Steuerung der Lüfter und Leistungslimits von ASUS-ROG-Laptops:
eigene Lüfterkurven pro Energieprofil, Vollgas-Modus, Profilwechsel und CPU/GPU-Leistungslimits.
Ein kleiner systemd-Dienst hält die Einstellungen über Profilwechsel, Standby und Neustart hinweg aktiv.

*English summary: GTK4/libadwaita fan and power-limit control for ASUS ROG laptops on Linux
(custom fan curves per platform profile, full-speed mode, PL1/PL2 and NVIDIA limits).
UI language is German. Tested on a single device only, see below.*

> [!WARNING]
> **Nur auf einem einzigen Gerät getestet** (siehe [Testgerät](#testgerät)). Das Verhalten der
> ASUS-Firmware unterscheidet sich je nach Modell und BIOS-Version. Auf anderen Geräten kann die
> App falsch funktionieren oder gar nicht. Sie schreibt direkt in Kernel-Schnittstellen für Lüfter
> und Leistung. Nutzung auf eigenes Risiko, ohne Gewähr (siehe [LICENSE](LICENSE)).

## Testgerät

Getestet ausschließlich auf diesem Gerät und dieser Software:

| | |
|---|---|
| Gerät | ASUS ROG Strix G16 (2023), Modell **G614JI** (`ROG Strix G614JI_G614JI`) |
| BIOS | G614JI.333 (01.10.2025) |
| CPU | Intel Core i7-13650HX |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU, proprietärer Treiber 620.70.06 |
| System | Fedora Linux 45 Workstation, Kernel 7.3.4-300.fc45 |
| Desktop | GNOME Shell 51.1 (Wayland) |
| Bibliotheken | Python 3.15.2, GTK 4.24.1, libadwaita 1.10.0, polkit 127 |
| Energieprofile | tuned-ppd 2.28.0 (mit thermald und nvidia-powerd) |

Andere ROG/TUF-Modelle mit denselben Kernel-Schnittstellen (`asus_custom_fan_curve`,
`asus-armoury`) könnten funktionieren. Das ist aber **nicht geprüft**. Die App ist darauf
vorbereitet: Sie erkennt Anzahl und Art der Lüfter, die Zahl der Kurvenpunkte, die verfügbaren
Energieprofile und Leistungslimits zur Laufzeit und blendet aus, was ein Gerät nicht kann.
Rückmeldungen per Issue sind willkommen, am besten mit dem **Gerätebericht** aus dem Menü.

## Funktionen

- **Einrichtung beim ersten Start** (später über Menü → „Gerät einrichten …“):
  - erkennt Lüfter, Kurvenpunkte, Profile und Limits und zeigt, ob das Gerät kompatibel ist
  - prüft harmlos, ob die Firmware für Leistungslimits eine aktive Kurve verlangt
  - liest die Werkskurven aller Energieprofile ein (schaltet dafür kurz durch die Profile)
  - optional: misst die Höchstdrehzahl jedes Lüfters (je bis zu 25 s Vollgas, laut), prüft, ob
    Vollgas wirkt, und erkennt Firmware-Eigenheiten wie falsche Drehzahlwerte
  - erstellt einen **Gerätebericht** zum Kopieren in ein Issue (nur Hardware- und Treiberdaten)
- **Übersicht:** Live-Verlauf (5 Minuten) von CPU- und GPU-Temperatur und allen Lüftern, Drehzahl
  auch in % der gemessenen Höchstdrehzahl. Die GPU-Temperatur wird nur abgefragt, wenn die GPU
  ohnehin wach ist, damit sie schlafen kann.
- **Lasttest:** CPU, GPU oder beides voll auslasten, um Kurven und Limits zu prüfen. CPU-Last mit
  einem Prozess pro Thread, GPU-Last mit einem kleinen CUDA-Rechenkern direkt über den
  NVIDIA-Treiber (`libcuda`, keine Zusatzpakete). Stoppt automatisch nach 1–30 Minuten und beim
  Schließen der App. Braucht keine Administratorrechte.
- **Energieprofil:** Leise / Ausgewogen / Leistung. Synchron mit dem GNOME-Energiemodus und Fn+F5,
  läuft über den PowerProfiles-D-Bus.
- **Vollgas:** pro Lüfter, bis zum Abschalten oder Neustart.
- **Lüfterkurven:** grafischer Editor pro Lüfter und Profil (Punktzahl wie von der Firmware
  vorgegeben, auf dem Testgerät 8). Die Werkskurve wird zum
  Vergleich gestrichelt angezeigt, dazu die aktuelle Temperatur. Vorlagen: Leise, Ausgewogen,
  Kühl, Werkskurve.
- **Leistungslimits pro Profil:** CPU PL1/PL2, NVIDIA Dynamic Boost, NVIDIA-Temperaturziel.
- **Hinweise auf Konflikte:** zum Beispiel wenn asusd, power-profiles-daemon und tuned-ppd
  gleichzeitig laufen, nvidia-powerd fehlt oder eine Kurve gerade nicht aktiv ist.
- **Sicherheitsprüfung:** nur steigende Kurven, Temperaturen von 20 bis 100 °C, letzter Punkt
  mindestens 30 % Drehzahl, PL2 ≥ PL1. Der Kernel prüft selbst nichts davon.

## Aufbau

| Teil | Ort (RPM) | Aufgabe |
|---|---|---|
| Oberfläche | `rog-fan-control`, App-Menü „ROG Lüftersteuerung“ | läuft als Benutzer, liest nur |
| Helfer | `/usr/libexec/rog-fan-control/rog-fan-helper` | schreibt geprüfte Werte, gestartet via pkexec/polkit |
| Dienst | `rog-fan-control.service` | setzt Kurven und Limits bei Boot, Profilwechsel und nach Firmware-Reset |
| Konfiguration | `/etc/rog-fan-control/config.json` | eigene Kurven und Limits pro Profil |
| Werkskurven | `/var/lib/rog-fan-control/factory.json` | bei der Einrichtung und bei Profilwechseln eingelesen |
| Geräteprofil | `/var/lib/rog-fan-control/device.json` | Ergebnis der Einrichtung (Höchstdrehzahlen, Eigenheiten) |
| Lasttest | `/usr/libexec/rog-fan-control/rog-fan-load` | läuft als Benutzer, ohne Sonderrechte |

## Warum fragt die App nach dem Passwort?

Lüfterkurven, Vollgas und Leistungslimits werden über Kernel-Schnittstellen der ASUS-Firmware
gesetzt (`/sys/class/hwmon/…`, `/sys/class/firmware-attributes/asus-armoury/…`). Diese darf aus
Sicherheitsgründen nur root ändern – sonst könnte jedes Programm die Kühlung abschalten.

- Die **Oberfläche läuft ohne Sonderrechte**. Anzeige, Verlauf, Lasttest und Profilwechsel
  brauchen kein Passwort.
- Nur beim **Ändern** (Anwenden, Vollgas, Einrichtung, Dienst aktivieren) startet sie über polkit
  den Helfer `rog-fan-helper`. Er kennt nur wenige feste Befehle, nimmt ausschließlich geprüfte
  Werte an und führt keine beliebigen Befehle aus.
- Die Polkit-Aktion `org.local.rogfancontrol.helper` verlangt ein Administrator-Passwort und
  merkt es sich einige Minuten lang (`auth_admin_keep`).
- Der Hintergrunddienst läuft als root, damit er Kurven nach Profilwechseln und Neustarts ohne
  Rückfrage neu setzen kann.

## Voraussetzungen

- Kernel mit `asus-wmi` (hwmon `asus` und `asus_custom_fan_curve`) und `asus-armoury`
  (`/sys/class/firmware-attributes/asus-armoury`)
- Python 3, PyGObject, GTK 4, libadwaita ≥ 1.7, polkit, systemd
- für die GPU-Temperatur: `nvidia-smi`; für Dynamic Boost: `nvidia-powerd`; für den GPU-Lasttest:
  NVIDIA-GPU mit proprietärem Treiber (`libcuda`)
- **nicht gleichzeitig mit asusctl/asusd verwenden**, sonst überschreiben sich beide gegenseitig

## Installieren

**Als RPM** (Fedora): Das RPM aus dem neuesten [Release](../../releases) installieren:

```sh
sudo dnf install ./rog-fan-control-*.noarch.rpm
sudo systemctl enable --now rog-fan-control   # Fedora aktiviert neue Dienste nicht automatisch
```

**Ohne RPM** nach `/usr/local`: `sudo ./install.sh`, Entfernen mit `sudo ./uninstall.sh [--purge]`.
Das Entfernen setzt Lüfter und Limits auf Werkseinstellung zurück. Nicht beide Varianten
gleichzeitig installieren, sonst laufen zwei Dienste.

Log: `journalctl -u rog-fan-control`

## Bauen

```sh
make check   # Syntax, .desktop, Polkit-XML
make rpm     # -> build/rpm/RPMS/noarch/*.rpm
```

GitHub Actions baut bei jedem Push auf `main` das RPM (Artefakt „rpms“). Ein Tag `vX.Y.Z`, der zur
`Version:` in `rog-fan-control.spec` passt, erstellt ein Release mit den RPMs.

## Gemessenes Firmware-Verhalten (Testgerät)

Diese Punkte wurden auf dem Testgerät gemessen und stehen so nicht in der Kernel-Doku. Auf
anderen Modellen kann es anders sein.

- `asus_custom_fan_curve/pwmN_enable`: `1` = eigene Kurve, `2` = Firmware-Kurve,
  `3` = Firmware-Kurve und Werkspunkte des aktiven Profils laden (liest danach `2`).
- Der Kernel nimmt auch unsinnige Kurven an (nicht monoton, 101 °C). Die App prüft das deshalb selbst.
- Ein Profilwechsel schaltet eigene Kurven ab. Der Dienst setzt sie innerhalb von etwa 2 s neu.
- `asus/pwmN_enable`: `0` = Vollgas, `2` = Automatik. GPU- und Mittellüfter lesen immer `0`,
  deshalb wird der Zustand in `/run/rog-fan-control/state.json` mitgeführt.
- Im Vollgas-Modus meldet der mittlere Lüfter die Drehzahl mit gesetztem Bit 15
  (z. B. `3281200` statt `4400`). Die App rechnet das heraus.
- `asus-armoury` lehnt PL1/PL2-Änderungen mit `EBUSY` ab („PPT change requires an active fan
  curve“), solange keine eigene Kurve aktiv ist. Die App aktiviert dafür kurz die Werkskurve des
  Profils und schaltet sie danach wieder ab. Das Limit bleibt erhalten und wirkt: Unter Volllast
  sank die Leistung von 112 W (PL1 139 W) auf 45 W (PL1 35 / PL2 45 W).
- Die Leistungslimits bleiben über Profilwechsel hinweg stehen. Der Dienst setzt deshalb bei
  jedem Wechsel die Limits des neuen Profils.
- Die Werkskurven unterscheiden sich je Profil. Im Profil „Leise“ enthält die Firmware-Tabelle
  doppelte Temperaturpunkte (z. B. `63/2 63/2 … 75/38 75/38`). Das ist so hinterlegt und kein
  Lesefehler.
- Höchstdrehzahl (gemessen bei der Einrichtung): CPU 5300, GPU 6100, Mitte 4500 U/min.

## Lizenz

[MIT](LICENSE). Kein offizielles ASUS-Produkt, nicht mit ASUS verbunden.
