# basic mypy linting (no autocommit gauntlet, yet)

lint:	install
	venv/bin/mypy mc_deploy

install: venv/.pyproject

venv/.pyproject: pyproject.toml
	python -m venv venv
	cmp pyproject.toml venv/.pyproject || \
		venv/bin/pip install -e '.[mypy]' && \
		cp -p pyproject.toml venv/.pyproject

clean:
	rm -rf venv .mypy_cache build mc_deploy.egg-info mc_deploy/__pycache__ 
