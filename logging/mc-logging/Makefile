# not (yet) using pre-commit; would apply to entire repo!

help:
	@echo Usage:
	@echo "make install -- installs dev environment"
	@echo "make lint -- runs formatting, type checks"
	@echo "make release -- add tag and push to github"
	@echo "make clean -- remove development environment"
	@echo "make help -- this message!"

PKG=mc_logging

# story-indexer order: black, mypy, flake8, isort
lint:	install
	venv/bin/black $(PKG)
	venv/bin/mypy $(PKG)
	venv/bin/flake8 $(PKG)
	venv/bin/isort $(PKG)

install: venv/.pyproject

# tag and push using mc-logging itself!
release: lint
	venv/bin/python release.py release

# pip install -e not perfect; "installs" package in venv
venv/.pyproject: pyproject.toml
	python -m venv venv
	cmp pyproject.toml venv/.pyproject || \
		venv/bin/pip install -e '.[lint]' && \
		cp -p pyproject.toml venv/.pyproject

clean:
	rm -rf venv .mypy_cache build $(PKG).egg-info $(PKG)/__pycache__ 
