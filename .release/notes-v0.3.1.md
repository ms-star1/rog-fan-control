> [!WARNING]
> **Nur auf einem einzigen Gerät getestet:** ASUS ROG Strix G16 (2023), Modell **G614JI**, BIOS G614JI.333,
> Intel Core i7-13650HX, NVIDIA RTX 4070 Laptop (Treiber 620.70.06), Fedora Linux 45 Workstation,
> Kernel 7.3.4-300.fc45, GNOME 51.1. Andere Modelle oder BIOS-Versionen können sich anders verhalten.
> Nutzung auf eigenes Risiko.

GTK4/libadwaita-App für eigene Lüfterkurven pro Energieprofil, Vollgas-Modus, Profilwechsel und
CPU/GPU-Leistungslimits auf ASUS-ROG-Laptops, mit systemd-Dienst, der die Einstellungen aktiv hält.

### Neu in 0.3.1
- **Diagramme immer im Blick:** kompakte, feste Leiste unter der Kopfzeile, in jedem Tab sichtbar;
  der Inhalt scrollt darunter durch.
- **Immer nebeneinander:** Bei schmalem Fenster wird die Leiste seitlich scrollbar, mit Pfeiltasten
  links und rechts (am Ende ausgegraut, im breiten Fenster ausgeblendet).

### Bisher
- 0.3.0: Taktfrequenz-Diagramm, Hinweis bei abgeschaltetem Turbo-Boost
- 0.2.1: Leistungsaufnahme in Watt (CPU, GPU, System im Akkubetrieb)
- 0.2.0: Einrichtungsassistent, Geräteerkennung für kompatible Modelle, Lasttest, Gerätebericht

### Installieren (Fedora)
```sh
sudo dnf install ./rog-fan-control-0.3.1-1.fc45.noarch.rpm
sudo systemctl enable --now rog-fan-control
```
Nicht gleichzeitig mit asusctl/asusd verwenden.
