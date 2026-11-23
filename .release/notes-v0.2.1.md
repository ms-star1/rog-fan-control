> [!WARNING]
> **Nur auf einem einzigen Gerät getestet:** ASUS ROG Strix G16 (2023), Modell **G614JI**, BIOS G614JI.333,
> Intel Core i7-13650HX, NVIDIA RTX 4070 Laptop (Treiber 620.70.06), Fedora Linux 45 Workstation,
> Kernel 7.3.4-300.fc45, GNOME 51.1. Andere Modelle oder BIOS-Versionen können sich anders verhalten.
> Nutzung auf eigenes Risiko.

GTK4/libadwaita-App für eigene Lüfterkurven pro Energieprofil, Vollgas-Modus, Profilwechsel und
CPU/GPU-Leistungslimits auf ASUS-ROG-Laptops, mit systemd-Dienst, der die Einstellungen aktiv hält.

### Neu in 0.2.1
- **Leistungsaufnahme in Watt** im Verlauf: CPU-Paket (RAPL, vom Dienst als Sekundenmittel
  bereitgestellt), GPU (nvidia-smi) und Gesamtsystem im Akkubetrieb.

### Bisher (0.2.0)
- Einrichtungsassistent mit Geräteerkennung, Werkskurven aller Profile und optionaler Messung der
  Höchstdrehzahl; Gerätebericht für Issues
- Lüfter, Kurvenpunkte, Profile und Limits werden erkannt statt fest vorgegeben
- Lasttest für CPU, GPU oder beides
- Erklärung, warum die App ein Administrator-Passwort braucht

### Installieren (Fedora)
```sh
sudo dnf install ./rog-fan-control-0.2.1-1.fc45.noarch.rpm
sudo systemctl enable --now rog-fan-control
```
Nicht gleichzeitig mit asusctl/asusd verwenden.
