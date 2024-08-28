PROG=prune-backups
DESTBASE=/opt/MC

DIR=$(DESTBASE)/$(PROG)
VENV=$(DIR)/venv
VENVDONE=$(VENV)/.done
PY=$(PROG).py

install: $(VENVDONE)
	echo "installed from system-dev-ops repo" > $(DIR)/README
	cp -p $(PY) $(DIR)
	chown root:root $(DIR)/$(PY)

uninstall:
	rm -rf $(DIR)

$(VENVDONE): requirements.txt
	test -d $(DIR) || mkdir -p $(DIR)
	test -d $(VENV) || (cd $(DIR); python -mvenv venv)
	$(VENV)/bin/python -m pip install -r requirements.txt
