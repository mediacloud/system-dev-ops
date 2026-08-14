"""
Simple UDP syslog sink; writes to hourly log files
[from story-indexer]

Implements simple legacy BSD syslog protocol
(implemented in python SysLogHandler)
but does NOT expect legacy date/time format!!

web-search has a fork of story-indexer original that reads a logging
config file, and routes messages to loggers named "facility_N" to
enable logging with different formats to different files.  Merge that
in and feed three birds (rss-fetcher, story-indexer and web-search)
with one scone?

XXX use SENTRY_DSN if set, for fatal errors so not screaming into the void?
[having sentry enabled ALL the time might mean multiple copies sent to sentry!]
"""

import logging
import os
import socket
import sys
import time
from logging.handlers import SysLogHandler  # for LOG_XXX

from mc_logging.common import syslog_path, syslog_port

# byte values:
NUL = 0
LT = ord("<")

MAXMSG = 64 * 1024
DEFPRIO = logging.INFO

# map syslog priorities to Python logging levels
SYSLOG2LOGGING = {
    SysLogHandler.LOG_EMERG: logging.CRITICAL,
    SysLogHandler.LOG_ALERT: logging.CRITICAL,
    SysLogHandler.LOG_CRIT: logging.CRITICAL,
    SysLogHandler.LOG_ERR: logging.ERROR,
    SysLogHandler.LOG_WARNING: logging.WARNING,
    SysLogHandler.LOG_NOTICE: logging.WARNING,
    SysLogHandler.LOG_INFO: logging.INFO,
    SysLogHandler.LOG_DEBUG: logging.DEBUG,
}


def parse_msg(msg: bytes) -> tuple[int, bytes]:
    """
    parse message sent by SysLogHandler
    (could create our own format with string
    facility name to direct to a named logger)
    """
    if msg[-1] == NUL:
        msg = msg[:-1]

    if msg[0] != LT or b">" not in msg[1:]:
        return (DEFPRIO, msg)

    pb, msg = msg[1:].split(b">", 1)
    prio = int(pb)
    return (prio, msg)


def main() -> None:
    # XXX implement options? min-level, log-dir, file-days

    # init logging to file:

    # get log dir, create if needed
    log_dir = os.environ.get("LOG_DIR")
    if not log_dir:
        data_dir = os.environ.get("DATA_DIR")
        if data_dir:
            log_dir = os.path.join(data_dir, "logs")
        else:
            log_dir = "/tmp"
            sys.stderr.write(
                f"Neither LOG_DIR nor DATA_DIR set: logging to {log_dir}\n"
            )

    os.makedirs(log_dir, exist_ok=True)

    fname = os.path.join(log_dir, "messages.log")

    days = int(os.environ.get("LOGFILE_DAYS", "14"))

    # new log file every hour
    # (XXX take option to rotate at midnight?)
    # NOTE!! rotates based on the minute the sink started?!
    # [Phil: I've always wanted to fix this!!!]
    handler = logging.handlers.TimedRotatingFileHandler(
        fname, when="h", utc=True, backupCount=days * 24
    )

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    # log everything sent here (XXX have min-level??)
    root_logger.setLevel(logging.DEBUG)

    if syslog_path:
        if os.path.exists(syslog_path):
            os.unlink(syslog_path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(syslog_path)
        listening_on = syslog_path
    elif syslog_port:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", int(syslog_port)))
        listening_on = syslog_port
    else:
        # configure sentry and log??
        sys.stderr.write("neither SYSLOG_PATH nor SYSLOG_PORT configured\n")
        time.sleep(5 * 60)  # avoid spinning
        sys.exit(1)

    logging.info(
        "mc_logging.sink listening on %s writing %s", listening_on, fname
    )

    while True:
        msg, addr = s.recvfrom(MAXMSG)
        facpri, msg = parse_msg(msg)

        if not msg:
            continue  # ignore if empty message

        ipri = facpri & SysLogHandler.LOG_DEBUG  # input prio
        logpri = SYSLOG2LOGGING[ipri]  # map to Python logging prio

        # send decoded message into local logging code!!
        logging.log(logpri, msg.decode("utf-8"))


if __name__ == "__main__":
    main()
