# the barest of system stats
# adapted from https://github.com/blackrosezy/statsd-agent
# Phil Budne
# March 2024

import os
import socket
import time

import statsd
import psutil

INTERVAL = 60                   # seconds
STATSD_HOST = "tarbell.angwin"

DISKS = [
    ("/", "root"),
    ("/srv/data", "srv_data"),
    ("/space", "space"),
]

host = socket.gethostname().split(".")[0]

def report(f):
    for path, name in DISKS:
        if not os.path.exists(path):
            continue
        disk_usage = psutil.disk_usage(path)
        f(f"disk.pct.{host}.{name}", disk_usage.percent)

    
    f(f"cpu.pct.{host}", psutil.cpu_percent(interval=None))

    swap = psutil.swap_memory()
    f(f"swap.pct.{host}", swap.percent)

    virtual = psutil.virtual_memory()
    f(f"vm.pct.{host}", virtual.percent)

    la = os.getloadavg()
    f(f"load.1.{host}", la[0])
    f(f"load.5.{host}", la[1])
    f(f"load.15.{host}", la[2])

while True:
    c = statsd.StatsdClient(STATSD_HOST, 8125, prefix="mc.systems")
    f = c.gauge
    #f = print

    report(f)
   
    sleep_sec = INTERVAL - time.time() % INTERVAL
    time.sleep(sleep_sec)

