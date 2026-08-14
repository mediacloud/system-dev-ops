import os

syslog_path = os.environ.get("SYSLOG_PATH")  # Unix domain socket
syslog_host = os.environ.get("SYSLOG_HOST")  # UDP dest
syslog_port = os.environ.get("SYSLOG_PORT")  # UDP dest
