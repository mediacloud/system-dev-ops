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

def full_diskstats():
    # psutil disk_io_counters is incomplete!
    ret = {}
    with open("/proc/diskstats") as f:
        for line in f:
            # docs are one-based in terms of fields after the name:
            # 0 & 1 are major and minor device numbers?
            toks = line.strip().split()[2:]
            device = toks[0]
            if device.startswith("loop"):
                continue
            ret[device] = devstats = {}
            # discard are TRIM discard ops
            ops = devstats["ops"] = {}
            for op, start in [("read", 1), ("write", 5), ("discard", 12)]:
                ops[op] = {
                    "completed": int(toks[start]),
                    "merged": int(toks[start+1]),
                    "kb": int(toks[start+2]) // 2, # sectors to kB
                    "ms": int(toks[start+3])
                }
            # not per disk:
            devstats["in-progress"] = int(toks[9])
            devstats["time-busy"] = int(toks[10])
            devstats["weighted-time"] = int(toks[11]) # qlen x time product?
            devstats["flush-completed"] = int(toks[16])
            devstats["flush-ms"] = int(toks[17])

    return ret

def report(f, prev, curr):
    """
    takes function to report or print a gauge
    """
    curr['time'] = now = time.monotonic()
    if prev:
        dt = (now - prev['time']) * 1000 # ms
    else:
        dt = 0

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

    # OLD: remove once grafana switched over
    diskstats = psutil.disk_io_counters(perdisk=True)
    for dev, stats in diskstats.items():
        if dev in dev_to_fs:
            fs = DISKS[dev_to_fs[dev]]
            for field in stats._fields:
                name, unit = field.replace("_", "-").rsplit("-", 1)
                # group like units together
                f(f"disk.stats.{unit}.{name}.{host}.{fs}", getattr(stats, field))

    # NEW: psutil disk_io_counters are incomplete. Created this after
    # I saw Zabbix, and read
    # https://kernel-internals.org/io/observability/
    # and couldn't find anything off the shelf....

    # Doing deltas and calculations here because it's too much of a
    # pain in graphite queries (and need to know the reporting interval),

    # trying to stick to what "iostat -x" reports and not make
    # anything up!
    fulldisk = curr['disk'] = full_diskstats()
    prevdisk = prev.get('disk')
    if prevdisk and dt:
        for dev, stats in fulldisk.items():
            if dev not in dev_to_fs:
                continue        # not a mounted device

            fspath = dev_to_fs[dev] # get mount location
            if fspath not in DISKS: # no mapping to stats name?
                continue            # complain????
            fsname = DISKS[fspath]

            p = prevdisk[dev]

            def report(op, stat, value):
                f(f"disk.nstats.{op}.{stat}.{host}.{fs}", value)

            pops = p["ops"]
            for op, counts in stats["ops"].items():
                # op is read/write/discard
                # counts is dict with complete, merged, sectors, time

                pcounts = pops[op]      # prev counts
                d_count = counts["completed"] - pcounts["completed"]
                d_kb = counts["kb"] - pcounts["kb"]
                d_ms = counts["ms"] - pcounts["ms"]
                d_merged = counts["merged"] - pcounts["merged"]

                if d_count:
                    avg_kb = d_kb / d_count
                    pct_merged = 100 * d_merged / d_count
                else:
                    avg_kb = 0
                    pct_merged = 0

                report(op, "reqs-sec", d_count/dt) # requests per second
                report(op, "kb-sec", d_kb/dt) # kBbytes per second
                report(op, "avg-wait-ms", d_ms/dt) # avg wait in ms
                report(op, "avg-kb", avg_kb) # avg request size in kB
                report(op, "pct-merged", pct_merged) # indicates seqential access

            # remainder not per-operation:
            d_flushes = stats["flush-completed"] - p["flush-completed"]
            d_flush_ms = stats["flush-ms"] - p["flush-ms"]

            # not operation with full stats, but using same names:
            report("flush", "reqs-sec", d_flushes/dt) # flushes/second
            report("flush", "avg-wait-ms", d_flush_ms/dt) # avg flush wait time

            d_weighted = stats["weighted-time"] - p["weighted-time"]
            d_busy = stats["time-busy"] - p["time-busy"]
            report("overall", "queue-avg-len", d_weighted/dt)
            report("overall", "in-progress", stats["in-progress"]) # instantaneous FWIW
            report("overall", "utilization", 100*d_busy/dt)

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
prev = {}
while True:
    # PB: why did I put this inside the loop? in case don't start up correctly??
    c = statsd.StatsdClient(STATSD_HOST, 8125, prefix="mc.systems")
    if "--debug" in sys.argv:
        f = print
        INTERVAL = 10
    else:
        f = c.gauge

    curr = {}
    report(f, prev, curr)
    prev = curr
   
    sleep_sec = INTERVAL - time.time() % INTERVAL
    time.sleep(sleep_sec)
