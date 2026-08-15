"""
Send log messages from all containers/processes/threads
to a single log file written by mc_logging.sink

Using Unix syslog protocol (but have avoided putting that
in the file/class/method names)!

From mediaclud/story-indexer/indexer/app.py
Phil Budne 8/14/2026
"""

import logging
import logging.handlers
import os
import socket
import sys
import time
from typing import Any

# local:
from mc_logging.common import syslog_host, syslog_path, syslog_port

# look like syslog messages (except date format),
# adds levelname; does NOT include logger name, or pid:
NORMAL_SYSLOG_FORMAT = (
    "%(asctime)s %(hostname)s %(app)s %(levelname)s: %(message)s"
)
# include thread, formatted as if syslog pid:
THREAD_SYSLOG_FORMAT = "%(asctime)s %(hostname)s %(app)s[%(threadName)s] %(levelname)s: %(message)s"
# include supplied subid as if a syslog pid:
SUBID_SYSLOG_FORMAT = (
    "%(asctime)s %(hostname)s %(app)s[%(subId)s] %(levelname)s: %(message)s"
)


# not a subclass of socket.socket due to signature forgery issues
class SendtoSocketWrapper:
    """
    Wrapper for UDP sockets used in logging.handlers.SysLogHandler and
    statsd.StatsdClient, so that socket.sendto doesn't do a DNS lookup
    on EVERY call (OR use the address resolved at startup forever,
    since it's almost CERTAINLY a container, and could be replaced at any time).
    """

    def __init__(self, actual_socket: socket.socket, cache_sec: int = 60):
        """
        defaults to caching for 60 seconds, so if address changes,
        will lose at most one minute of traffic
        """
        assert actual_socket.family == socket.AF_INET
        assert actual_socket.type == socket.SOCK_DGRAM
        self.actual_socket = actual_socket
        self.last_host = ""
        self.last_addr = ""
        self.last_lookup = 0.0
        self.cache_sec = cache_sec

    def sendto(self, data: bytes, to: tuple[str, int]) -> int:
        """
        both SysLogHandler and Statsd only call with two args
        """
        # uses VDSO (no context switch) on x86-64 systems (at least)
        # if it's a problem, only check every N calls

        # [Phil: I forget why I didn't "connect" the socket
        # and avoid the overhead of passing in the address and port
        # each time!  Maybe the story-indexer git logs show why?]

        now = time.monotonic()
        if now - self.last_lookup > self.cache_sec:
            self.last_addr = ""  # invalidate cache

        to_host = to[0]
        if to_host != self.last_host or not self.last_addr:
            self.last_host = to_host
            # IPv4 only, returns single addr (round robin):
            self.last_addr = socket.gethostbyname(to_host)
            self.last_lookup = now

        return self.actual_socket.sendto(data, (self.last_addr, to[1]))

    def close(self) -> None:
        """
        called on app shutdown?
        """
        self.actual_socket.close()


class SysLogHandler(logging.handlers.SysLogHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        pass


_saved_handlers: dict[int, SysLogHandler] = {}


def log_to_sink(
    app: str,  # program/process name
    *,
    sub_id: str | None = None,
    add_to_root_logger: bool = True,
    log_thread_id: bool = False,
    facility: int = SysLogHandler.LOG_LOCAL0,
    format: str | None = None,
    overrides: dict[str, Any] = {},
    startup_delay: float = 5.0,  # zero for no delay
) -> SysLogHandler | None:
    # NOTE!! Using unreliable UDP because TCP connection backlog
    # can cause sends to socket to block!!

    # fork of story-indexer code in web-search supports multiple log
    # files with different formats via different LOCALn facilities.
    # (so if that's needed, merge that to this file, and have
    # web-search use it!!!)
    if syslog_path:
        handler = SysLogHandler(address=syslog_path, facility=facility)
        if startup_delay:
            wait_for_sink(syslog_path, startup_delay)
    elif syslog_host and syslog_port:
        handler = SysLogHandler(
            address=(syslog_host, int(syslog_port)), facility=facility
        )
        # wrap to avoid DNS lookup on each message!!!
        handler.socket = SendtoSocketWrapper(handler.socket)  # type: ignore[attr-defined]
    else:
        # tempting to log, but requires a terminal logger is set up
        # (ie; basicConfig called)
        sys.stderr.write("WARNING: no valid syslog destination\n")
        return None

    if format is None:
        if sub_id:
            format = SUBID_SYSLOG_FORMAT
        elif log_thread_id:
            format = THREAD_SYSLOG_FORMAT
        else:
            format = NORMAL_SYSLOG_FORMAT

    # additional items available to format string
    # XXX take additional values as argument??
    defaults = {
        "hostname": socket.gethostname(),  # without domain
        "app": app,
    }
    if sub_id:
        defaults["subId"] = sub_id

    if overrides:
        # could be used with user supplied format
        defaults.update(overrides)

    # Might like default datefmt includes milliseconds
    # (which aren't otherwise available)
    formatter = logging.Formatter(fmt=format, defaults=defaults)
    handler.setFormatter(formatter)

    if add_to_root_logger:
        root_logger = logging.getLogger()
        prev = _saved_handlers.get(facility)
        if prev:
            root_logger.removeHandler(prev)
        _saved_handlers[facility] = handler
        root_logger.addHandler(handler)

    return handler


def wait_for_sink(path: str, startup_delay: float) -> None:
    """
    called to wait for startup of unix-domain log sink
    """
    orig_ctime = -1.0
    delayed = 0.0
    sleep_time = 0.5
    while True:
        try:
            st = os.stat(path)
            if orig_ctime == -1:
                orig_ctime = st.st_ctime
                if time.time() - orig_ctime < 60.0:
                    # less than a minute old
                    break
            elif st.st_ctime > orig_ctime:
                return
        except OSError:
            pass

        if delayed > startup_delay:
            break

        time.sleep(sleep_time)
        delayed += sleep_time
    else:
        sys.stderr.write(
            f"did not see new log socket within {delayed} seconds\n"
        )


if __name__ == "__main__":
    logging.basicConfig()

    # basic development test
    log_to_sink("testing")
    tl = logging.getLogger("test")
    tl.setLevel(logging.DEBUG)
    tl.info("info")
    tl.warning("warning")
    tl.error("error")
