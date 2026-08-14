"""
From mediaclud/story-indexer/indexer/app.py
"""

import logging
import socket
import time
from logging.handlers import SysLogHandler

# look like syslog messages (except date format),
# adds levelname; does NOT include logger name, or pid:
NORMAL_SYSLOG_FORMAT = (
    "%(asctime)s %(hostname)s %(app)s %(levelname)s: %(message)s"
)
# include thread, formatted as if syslog pid:
THREAD_SYSLOG_FORMAT = "%(asctime)s %(hostname)s %(app)s[%(threadName)s] %(levelname)s: %(message)s"


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


def log_to_sink(
    app: str,  # program/process name
    syslog_host: str,
    syslog_port: str,
    *,
    add_to_root_logger: bool = True,
    log_thread_id: bool = False,
) -> SysLogHandler | None:
    if not syslog_host or not syslog_port:
        return None

    # NOTE!! Using unreliable UDP because TCP connection backlog
    # can cause sends to socket to block!!

    # fork of story-indexer code in web-search supports multiple log
    # files with different formats via different LOCALn facilities.
    # (so if that's needed, merge that to this file, and have
    # web-search use it!!!)
    handler = SysLogHandler(
        address=(syslog_host, int(syslog_port)),
        facility=SysLogHandler.LOG_LOCAL0,
    )
    handler.socket = SendtoSocketWrapper(handler.socket)  # type: ignore[attr-defined]

    if log_thread_id:
        fmt = THREAD_SYSLOG_FORMAT
    else:
        fmt = NORMAL_SYSLOG_FORMAT

    # additional items available to format string:
    defaults = {
        "hostname": socket.gethostname(),  # without domain
        "app": app,
    }

    # Might like default datefmt includes milliseconds
    # (which aren't otherwise available)
    formatter = logging.Formatter(fmt=fmt, defaults=defaults)
    handler.setFormatter(formatter)

    if add_to_root_logger:
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

    return handler
