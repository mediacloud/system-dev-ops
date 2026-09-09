# not (yet) using pre-commit; would apply to entire repo!

VENVDIR=.venv
VENVBIN=$(VENVDIR)/bin

help:
	@echo Usage:
	@echo "make install -- installs dev environment"
	@echo "make lint -- runs formatting, type checks"
	@echo "make release -- add tag and push to github"
	@echo "make clean -- remove development environment"
	@echo "make list-outdated -- list outdated requirements"
	@echo "make help -- this message!"

# story-indexer order: black, mypy, flake8, isort
lint:	install
	$(VENVBIN)/black mc_deploy
	$(VENVBIN)/mypy mc_deploy
	$(VENVBIN)/flake8 mc_deploy
	$(VENVBIN)/isort mc_deploy

VENVPROJ=$(VENVDIR)/.pyproject
install: $(VENVPROJ)

# tag and push using mc-deploy itself!
release: lint
	$(VENVBIN)/python release.py release

$(VENVPROJ): pyproject.toml
	cmp pyproject.toml $(VENVPROJ) || \
		uv sync --extra lint && \
		cp -p pyproject.toml $(VENVPROJ)

# list outdated requirements
# https://github.com/astral-sh/uv/issues/6794
# discusses automagic update
list-outdated:
	uv pip list --outdated

clean:
	rm -rf $(VENVDIR) .mypy_cache mc_deploy.egg-info mc_deploy/__pycache__
