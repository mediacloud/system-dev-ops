# XXX should re-check container addr regularly!
"""
Program to proxy for a statsd-graphite-grafana container.

Dokku doesn't make it easy to expose a container's UDP port on the
host server....

Maybe run this in a home-backed container?

Or as a Dokku app setup to use docker-options:
https://dokku.com/docs/networking/port-management/#dockerfile

        which suggests:

        dokku proxy:disable myapp

        dokku docker-options:add myapp deploy "-p 2456:2456/udp"

Which would avoid the need to do docker inspect (could use Docker DNS?)
"""

import json
import logging
import logging.handlers
import os
import socket
import sys
import time

NAME = 'statsd-proxy'

STATSD_PORT = 8125

logger = logging.getLogger(NAME)

def udp_proxy(listen_port, dest_host_port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind( ('0.0.0.0', listen_port) )
    # COULD connect socket to dest_host_port, but UDP
    # implementations are allowed to demux packets based on remote host/port.

    #print("dest_host_port", dest_host_port)
    while True:
        b, sender = s.recvfrom(32 * 1024)
        s.sendto(b, 0, dest_host_port)
        #logger.info("%s:%s: %s", sender[0], sender[1], b.decode('ascii'))

def inspect_container(name):
    while True:
        with os.popen(f"docker inspect {name}") as f:
            return json.load(f)

 
def main():
    init_logging()
    if len(sys.argv) > 1:
        name = sys.argv[1]
    else:
        name = 'dokku.graphite.ObscureStatsServiceName'

    while True:
        j = inspect_container(name)
        if j:
            break
        time.sleep(10)

    addr = j[0]['NetworkSettings']['IPAddress']
    logger.info("dest %s -> %s", name, addr)

    dest_host_port = (addr, STATSD_PORT)
    udp_proxy(STATSD_PORT, dest_host_port)

def init_logging():
    fname = NAME + '.log'

    # rotate file daily, after midnight (UTC)
    handler = \
        logging.handlers.TimedRotatingFileHandler(
            fname, when='h', utc=True,
            backupCount=2)

    format = '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    handler.setFormatter(logging.Formatter(format))

    root_logger = logging.getLogger(None)
    root_logger.addHandler(handler)

    logger.setLevel(logging.DEBUG)

if __name__ == '__main__':
    main()
