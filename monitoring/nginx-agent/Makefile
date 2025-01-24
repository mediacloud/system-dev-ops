PROG=nginx-agent
DESTBASE=/opt/MC
SYSTEMD_DIR=/etc/systemd/system

DIR=$(DESTBASE)/$(PROG)
VENV=$(DIR)/venv
VENVDONE=$(VENV)/.done
SVC=$(PROG).service
PY=$(PROG).py

STATUS_CONF=status.conf
STATUS_PATH=/etc/nginx/conf.d/$(STATUS_CONF)

install: $(VENVDONE)
	echo "installed from system-dev-ops repo" > $(DIR)/README
	cp -p $(STATUS_CONF) $(STATUS_PATH)
	cp -p $(PY) $(DIR)
	chown root:root $(DIR)/$(PY) $(STATUS_PATH)
	sed -e "s@DIR@$(DIR)@" -e "s@VENV@$(VENV)@" < $(SVC).in > $(SYSTEMD_DIR)/$(SVC)
	systemctl daemon-reload
	systemctl enable $(SVC)
	systemctl start $(SVC)
	systemctl reload nginx

uninstall:
	-systemctl stop $(SVC)
	-systemctl disable $(SVC)
	rm -f $(SYSTEMD_DIR)/$(SVC) $(STATUS_CONF)
	rm -rf $(DIR)
	systemctl reload nginx

$(VENVDONE): requirements.txt
	test -d $(DIR) || mkdir -p $(DIR)
	test -d $(VENV) || (cd $(DIR); python -mvenv venv)
	$(VENV)/bin/python -m pip install -r requirements.txt

status:
	systemctl status $(SVC)

restart:
	systemctl restart $(SVC)
