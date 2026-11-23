NAME       := rog-fan-control
VERSION    := $(shell sed -n 's/^Version:[[:space:]]*//p' $(NAME).spec)

PREFIX     ?= /usr/local
BINDIR     ?= $(PREFIX)/bin
LIBEXECDIR ?= $(PREFIX)/libexec/$(NAME)
DATADIR    ?= $(PREFIX)/share
UNITDIR    ?= /etc/systemd/system
POLKITDIR  ?= /usr/share/polkit-1/actions
SYSCONFDIR ?= /etc
DESTDIR    ?=

PY        := rogfan_hw.py rog_fan_gui.py
EXE       := rog-fan-helper rog-fan-daemon
APPID     := org.local.RogFanControl
GENERATED := build/$(NAME) build/$(NAME).service build/org.local.rogfancontrol.policy
DISTFILES := Makefile $(NAME).spec README.md $(PY) $(EXE) $(NAME).service.in \
             org.local.rogfancontrol.policy.in $(APPID).desktop $(APPID).svg \
             install.sh uninstall.sh

.PHONY: all check install dist rpm clean

all: $(GENERATED)

build:
	mkdir -p build

build/%: %.in | build
	sed 's|@LIBEXECDIR@|$(LIBEXECDIR)|g' $< > $@

build/$(NAME): | build
	printf '#!/bin/sh\nexec /usr/bin/python3 %s/rog_fan_gui.py "$$@"\n' '$(LIBEXECDIR)' > $@
	chmod 755 $@

check: all
	python3 -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' $(PY) $(EXE)
	desktop-file-validate $(APPID).desktop
	@if command -v xmllint >/dev/null; then xmllint --noout build/org.local.rogfancontrol.policy $(APPID).svg; fi
	! grep -n '@[A-Z]*@' $(GENERATED)

install: all
	install -d -m 755 $(DESTDIR)$(LIBEXECDIR)
	install -m 644 $(PY) $(DESTDIR)$(LIBEXECDIR)/
	install -m 755 $(EXE) $(DESTDIR)$(LIBEXECDIR)/
	install -D -m 755 build/$(NAME) $(DESTDIR)$(BINDIR)/$(NAME)
	install -D -m 644 build/$(NAME).service $(DESTDIR)$(UNITDIR)/$(NAME).service
	install -D -m 644 build/org.local.rogfancontrol.policy $(DESTDIR)$(POLKITDIR)/org.local.rogfancontrol.policy
	install -D -m 644 $(APPID).desktop $(DESTDIR)$(DATADIR)/applications/$(APPID).desktop
	install -D -m 644 $(APPID).svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/$(APPID).svg
	install -d -m 755 $(DESTDIR)$(SYSCONFDIR)/$(NAME)

dist: | build
	tar --transform 's,^,$(NAME)-$(VERSION)/,' -czf build/$(NAME)-$(VERSION).tar.gz $(DISTFILES)

rpm: dist
	rpmbuild -ba --define '_topdir $(CURDIR)/build/rpm' --define '_sourcedir $(CURDIR)/build' $(NAME).spec

clean:
	rm -rf build
