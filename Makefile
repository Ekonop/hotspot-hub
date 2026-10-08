PREFIX ?= /usr
APP = hotspot-hub

.PHONY: install uninstall package clean check

check:
	python3 -m py_compile app/hotspot-hub.py
	bash -n install.sh
	bash -n uninstall.sh
	bash -n scripts/setup-profile.sh
	bash -n helpers/hotspot-on.sh
	bash -n helpers/hotspot-off.sh
	@echo "check-ok"

install:
	./install.sh

uninstall:
	./uninstall.sh

package:
	./scripts/stage-and-build.sh

clean:
	cd packaging/arch && rm -f hotspot-hub.py hotspot-on.sh hotspot-off.sh hotspot-channelsync.sh 90-hotspot-channelsync hotspot-channelsync.service 99-hotspot.conf hotspot-hub.sudoers hotspot-hub.svg hotspot-hub.desktop setup-profile.sh *.pkg.tar.* *.src.tar.* 2>/dev/null; true
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
