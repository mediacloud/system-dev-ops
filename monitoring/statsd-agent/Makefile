PROG=statsd-agent
DESTBASE=/opt/MC
SYSTEMD_DIR=/etc/systemd/system

DIR=$(DESTBASE)/$(PROG)
VENV=$(DIR)/venv
VENVDONE=$(VENV)/.done
SVC=$(PROG).service
PY=$(PROG).py

install: $(VENVDONE)
	echo "installed from system-dev-ops repo" > $(DIR)/README
	cp -p $(PY) $(DIR)
	chown root:root $(DIR)/$(PY)
	sed -e "s@DIR@$(DIR)@" -e "s@VENV@$(VENV)@" < $(SVC).in > $(SYSTEMD_DIR)/$(SVC)
	systemctl daemon-reload
	systemctl enable $(SVC)
	systemctl start $(SVC)

uninstall:
	-systemctl stop $(SVC)
	-systemctl disable $(SVC)
	rm -f $(SYSTEMD_DIR)/$(SVC)
	rm -rf $(DIR)

$(VENVDONE): requirements.txt
	test -d $(DIR) || mkdir -p $(DIR)
	test -d $(VENV) || (cd $(DIR); python -mvenv venv)
	$(VENV)/bin/python -m pip install -r requirements.txt

status:
	systemctl status $(SVC)
