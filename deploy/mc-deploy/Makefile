# basic mypy linting (no autocommit gauntlet, yet)

# story-indexer order: black, mypy, flake8, isort
lint:	install
	venv/bin/black mc_deploy
	venv/bin/mypy mc_deploy
	venv/bin/flake8 mc_deploy
	venv/bin/isort mc_deploy

install: venv/.pyproject

# tag and push using mc-deploy itself!
release: lint
	venv/bin/python release.py release

venv/.pyproject: pyproject.toml
	python -m venv venv
	cmp pyproject.toml venv/.pyproject || \
		venv/bin/pip install -e '.[lint]' && \
		cp -p pyproject.toml venv/.pyproject

clean:
	rm -rf venv .mypy_cache build mc_deploy.egg-info mc_deploy/__pycache__ 
