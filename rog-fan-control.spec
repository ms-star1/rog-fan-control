Name:           rog-fan-control
Version:        0.3.0
Release:        1%{?dist}
Summary:        Fan curves, fan modes, power profiles and power limits for ASUS ROG laptops
License:        MIT
URL:            https://github.com/ms-star1/rog-fan-control
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
BuildRequires:  sed
BuildRequires:  python3
BuildRequires:  desktop-file-utils
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       polkit
Requires:       systemd
%{?systemd_requires}

%description
Tested only on an ASUS ROG Strix G16 G614JI (BIOS G614JI.333) with Fedora 45;
firmware behaviour differs between models, use on other devices at your own risk.

GTK4/libadwaita application to control the fans of ASUS ROG laptops through
the asus-wmi and asus-armoury kernel drivers: custom fan curves per power
profile, full-speed mode, platform profile switching and CPU/GPU power limits.
A small systemd service re-applies curves and limits on boot, after profile
changes and after firmware resets; a pkexec helper performs privileged writes.

%prep
%autosetup

%build
%make_build LIBEXECDIR=%{_libexecdir}/%{name}

%install
%make_install \
    PREFIX=%{_prefix} \
    LIBEXECDIR=%{_libexecdir}/%{name} \
    UNITDIR=%{_unitdir} \
    POLKITDIR=%{_datadir}/polkit-1/actions \
    SYSCONFDIR=%{_sysconfdir}

%check
make check LIBEXECDIR=%{_libexecdir}/%{name}

%post
%systemd_post %{name}.service

%preun
%systemd_preun %{name}.service

%postun
%systemd_postun_with_restart %{name}.service

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_libexecdir}/%{name}/
%{_unitdir}/%{name}.service
%{_datadir}/polkit-1/actions/org.local.rogfancontrol.policy
%{_datadir}/applications/org.local.RogFanControl.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.local.RogFanControl.svg
%dir %{_sysconfdir}/%{name}

%changelog
* Tue Nov 24 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.3.0-1
- Verlauf in vier Karten: Temperatur, Lüfterdrehzahl, Leistungsaufnahme und
  neu Taktfrequenz (CPU Ø/max, GPU), mit aktuellen Werten in der Legende
- Anordnung passt sich der Fensterbreite an: 4 nebeneinander, 2×2 oder
  untereinander; im schmalen Fenster wandert die Seitenumschaltung nach unten
- Hinweis, wenn der Turbo-Boost der CPU abgeschaltet ist

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.2.1-1
- Verlauf zeigt die Leistungsaufnahme in Watt: CPU-Paket (RAPL, über den
  Dienst als Sekundenmittel), GPU (nvidia-smi) und System (im Akkubetrieb)

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.2.0-1
- Einrichtungsassistent: Geräteerkennung, PL-Test, Werkskurven aller Profile,
  optionale Messung der Höchstdrehzahl, Gerätebericht für Issues
- Lüfter, Kurvenpunkte, Profile und Limits werden erkannt statt fest vorgegeben
- Lasttest für CPU, GPU (CUDA über den NVIDIA-Treiber) oder beides
- Menü mit Einrichtung, Gerätebericht und Info

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.3-1
- Drehzahl des mittleren Lüfters im Vollgas-Modus korrekt (Statusbit 0x8000)
- Verlaufsdiagramm robust gegen unplausible Drehzahlen

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.2-1
- MIT-Lizenz, Projekt-URL, ausführliche README mit Testgerät

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.1-1
- PL1/PL2 auch ohne eigene Lüfterkurve setzbar (Firmware verlangt aktive Kurve)
- Klarere Fehlermeldungen für Passwortabfrage, fehlenden Helfer und Schreibfehler

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.0-1
- Erste Version: GUI, pkexec-Helfer, systemd-Dienst
