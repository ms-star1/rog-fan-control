Name:           rog-fan-control
Version:        0.1.1
Release:        1%{?dist}
Summary:        Fan curves, fan modes, power profiles and power limits for ASUS ROG laptops
License:        LicenseRef-Proprietary
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
%doc README.md
%{_bindir}/%{name}
%{_libexecdir}/%{name}/
%{_unitdir}/%{name}.service
%{_datadir}/polkit-1/actions/org.local.rogfancontrol.policy
%{_datadir}/applications/org.local.RogFanControl.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.local.RogFanControl.svg
%dir %{_sysconfdir}/%{name}

%changelog
* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.1-1
- PL1/PL2 auch ohne eigene Lüfterkurve setzbar (Firmware verlangt aktive Kurve)
- Klarere Fehlermeldungen für Passwortabfrage, fehlenden Helfer und Schreibfehler

* Mon Nov 23 2026 ms-star1 <294934886+ms-star1@users.noreply.github.com> - 0.1.0-1
- Erste Version: GUI, pkexec-Helfer, systemd-Dienst
