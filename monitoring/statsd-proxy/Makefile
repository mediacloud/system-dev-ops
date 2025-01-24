PROG=statsd-proxy
DESTBASE=/opt/MC
SYSTEMD_DIR=/etc/systemd/system

DIR=$(DESTBASE)/$(PROG)
SVC=$(PROG).service
PY=$(PROG).py

install:
	test -d $(DIR) || mkdir $(DIR)
	echo "installed from system-dev-ops repo" > $(DIR)/README
	cp -p $(PY) $(DIR)
	chown root:root $(DIR)/$(PY)
	sed -e "s@DIR@$(DIR)@" < $(SVC).in > $(SYSTEMD_DIR)/$(SVC)
	systemctl daemon-reload
	systemctl enable $(SVC)
	systemctl start $(SVC)

uninstall:
	-systemctl stop $(SVC)
	-systemctl disable $(SVC)
	rm -f $(SYSTEMD_DIR)/$(SVC)
	rm -rf $(DIR)

status:
	systemctl status $(SVC)
