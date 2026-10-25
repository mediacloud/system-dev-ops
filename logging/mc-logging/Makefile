# not (yet) using pre-commit; would apply to entire repo!

VENVDIR=.venv
VENVBIN=$(VENVDIR)/bin
VENVDONE=$(VENVDIR)/.done

help:
	@echo Usage:
	@echo "make install -- installs dev environment"
	@echo "make lint -- runs formatting, type checks"
	@echo "make release -- add tag and push to github"
	@echo "make clean -- remove development environment"
	@echo "make help -- this message!"

PKG=mc_logging

uv.lock: pyproject.toml Makefile
	uv lock

mc-relock: uv.lock
	uv lock `awk '/git.https:\/\/github.com\/mediacloud\// { print }' pyproject.toml | sed -e 's/^ *"/--upgrade-package /' -e 's/ *@.*//'`

# pip install -e not perfect; "installs" package in venv
install $(VENVDONE): mc-relock
	uv sync --extra dev --extra lint
	touch $(VENVDONE)

# story-indexer order: black, mypy, flake8, isort
lint:	install
	$(VENVBIN)/black $(PKG)
	$(VENVBIN)/mypy $(PKG)
	$(VENVBIN)/flake8 $(PKG)
	$(VENVBIN)/isort $(PKG)

# tag and push using mc-logging itself!
release: lint
	$(VENVBIN)/python release.py release

clean:
	rm -rf $(VENVDIR) .mypy_cache build $(PKG).egg-info $(PKG)/__pycache__
