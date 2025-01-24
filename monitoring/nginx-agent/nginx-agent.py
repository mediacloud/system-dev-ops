# report nginx stats to statsd/graphite/grafana
# (using what's available in default nginx install in Ubuntu 22.04)
# Phil Budne
# January 2025

import os
import socket
import sys
import time

import statsd
import requests

INTERVAL = 60                   # seconds
STATSD_HOST = "tarbell.angwin"

host = socket.gethostname().split(".")[0]

HANDLED = "total.handled"
REQUESTS = "total.requests"
ACCEPTS = "total.accepts"

READING = "curr.reading"
WRITING = "curr.writing"
WAITING = "curr.waiting"

def report(f):
    try:
        # requires enabling; see status.conf in source dir
        resp = requests.get("http://127.0.0.1/nginx_status")

        # Current connection always:
        # shows up in connections, and writing and
        # increments accepts, handled, requests

        next = None
        # ['Active', 'connections:', '1', 'server', 'accepts', 'handled', 'requests', '223', '223', '449', 'Reading:', '0', 'Writing:', '1', 'Waiting:', '0']
        # collect tokens after "server" into server_stats?
        for token in resp.text.split():
            if token == "connections:":
                next = "connections"
                continue
            if token == "requests":
                next = ACCEPTS
                continue
            if token == "Reading:":
                next = READING
                continue
            if token == "Writing:":
                next = WRITING
                continue
            if token == "Waiting:":
                next = WAITING
                continue

            if token.isdigit() and next:
                f(next, int(token))
                if next == ACCEPTS:
                    next = HANDLED
                    continue
                if next == HANDLED:
                    next = REQUESTS
                    continue
            next = None
    except Exception as e:
        print(e)                # TEMP

while True:
    c = statsd.StatsdClient(STATSD_HOST, 8125, prefix=f"mc.nginx.{host}")
    if "--debug" in sys.argv:
        f = print
    else:
        f = c.gauge

    report(f)
   
    sleep_sec = INTERVAL - time.time() % INTERVAL
    time.sleep(sleep_sec)
