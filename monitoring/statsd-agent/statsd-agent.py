# the barest of system stats
# adapted from https://github.com/blackrosezy/statsd-agent
# Phil Budne
# March 2024
#
# NOTE! did not follow "tag_value" convention used elsewhere,
# and I've continued that reign of error. -phil

import os
import socket
import sys
import time

import statsd
import psutil

INTERVAL = 60                   # seconds
STATSD_HOST = "tarbell.angwin"

# map partition mount points to reporting names
DISKS = {
    "/": "root",
    "/srv/data": "srv_data",    # old ES cluster
    "/space": "space",          # tarbell
    "/data": "data",            # new ES cluster
    "/nfs/ang/users": "users"
}

host = socket.gethostname().split(".")[0]
dev_to_fs: dict[str, str] = {}
filesystems = set()             # subset of DISK keys found mounted

def get_devices():
    # find device names for mounted filesystems
    for disk in psutil.disk_partitions(all=True):
        if disk.mountpoint in DISKS and disk.device.startswith("/dev/"):
            devname = disk.device[5:]
            print("adding", devname, disk.mountpoint)
            dev_to_fs[devname] = disk.mountpoint
            filesystems.add(disk.mountpoint)

def report(f):
    for mount_point in filesystems:
        disk_usage = psutil.disk_usage(mount_point)
        name = DISKS[mount_point]
        f(f"disk.pct.{host}.{name}", disk_usage.percent)
        f(f"disk.total.{host}.{name}", disk_usage.total)
        f(f"disk.used.{host}.{name}", disk_usage.used)
        f(f"disk.free.{host}.{name}", disk_usage.free)

    f(f"cpu.pct.{host}", psutil.cpu_percent(interval=None))

    swap = psutil.swap_memory()
    f(f"swap.pct.{host}", swap.percent)

    virtual = psutil.virtual_memory()
    f(f"vm.pct.{host}", virtual.percent)

    la = os.getloadavg()
    f(f"load.1.{host}", la[0])
    f(f"load.5.{host}", la[1])
    f(f"load.15.{host}", la[2])

    diskstats = psutil.disk_io_counters(perdisk=True)
    for dev, stats in diskstats.items():
        if dev in dev_to_fs:
            fs = DISKS[dev_to_fs[dev]]
            for field in stats._fields:
                name, unit = field.replace("_", "-").rsplit("-", 1)
                # group like units together
                f(f"disk.stats.{unit}.{name}.{host}.{fs}", getattr(stats, field))

    cputimes = psutil.cpu_times()
    for field in cputimes._fields:
        name = field.replace("_", "-")
        f(f"cpu.state.{host}.{name}", getattr(cputimes, field))

    # scpustats(ctx_switches=470529163283, interrupts=109920968106, soft_interrupts=31224130583, syscalls=0)
    #print(psutil.cpu_stats())

    for ifname, stats in psutil.net_io_counters(pernic=True).items():
        # only physical, connected ethernet interfaces
        if ifname.startswith("en") and stats.bytes_recv > 0:
            f(f"net.bytes.tx.{host}.{ifname}", stats.bytes_sent)
            f(f"net.bytes.rx.{host}.{ifname}", stats.bytes_recv)

            f(f"net.pkts.rx.ok.{host}.{ifname}", stats.packets_recv)
            f(f"net.pkts.rx.err.{host}.{ifname}", stats.errin)
            f(f"net.pkts.rx.drop.{host}.{ifname}", stats.dropin)

            f(f"net.pkts.tx.ok.{host}.{ifname}", stats.packets_sent)
            f(f"net.pkts.tx.err.{host}.{ifname}", stats.errout)
            f(f"net.pkts.tx.drop.{host}.{ifname}", stats.dropout)

get_devices()
while True:
    c = statsd.StatsdClient(STATSD_HOST, 8125, prefix="mc.systems")
    if "--debug" in sys.argv:
        f = print
    else:
        f = c.gauge

    report(f)
   
    sleep_sec = INTERVAL - time.time() % INTERVAL
    time.sleep(sleep_sec)
