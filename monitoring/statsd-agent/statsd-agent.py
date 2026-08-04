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
from typing import NamedTuple

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

class DiskOpStats(NamedTuple):
    completed: int
    merged: int
    kB: int
    ms: int

def full_diskstats():
    """
    psutil disk_io_counters is incomplete!
    see https://www.kernel.org/doc/Documentation/admin-guide/iostats.rst
    """
    ret = {}
    with open("/proc/diskstats") as f:
        for line in f:
            toks = line.strip().split()[2:] # discard major/minor
            device = toks[0]
            if device.startswith("loop"):
                continue
            ret[device] = devstats = {}
            # "discard" are TRIM discard ops?
            ops = devstats["ops"] = {}
            for op, start in [("read", 1), ("write", 5), ("discard", 12)]:
                ops[op] = DiskOpStats(
                    completed=int(toks[start]), # requests completed
                    merged=int(toks[start+1]),  # requests merged (not in compl.)
                    kB=int(toks[start+2]) // 2, # convert sectors to kB
                    ms=int(toks[start+3])       # total ms for all requests
                )
            # not per disk:
            devstats["in-progress"] = int(toks[9])

            # "# of milliseconds spent doing I/Os (unsigned int)
            #  This field increases so long as field 9 is nonzero."
            devstats["time-busy"] = int(toks[10]) # ms

            # "This field is incremented at each I/O start, I/O completion, I/O
            #  merge, or read of these stats by the number of I/Os in progress
            #  (field 9) times the number of milliseconds spent doing I/O since the
            #  last update of this field.  This can provide an easy measure of both
            #  I/O completion time and the backlog that may be accumulating."
            devstats["weighted-time"] = int(toks[11]) # in-progress x time

            # not tracked for partitions:
            devstats["flush-completed"] = int(toks[16])
            devstats["flush-ms"] = int(toks[17])
    return ret

def report(f, prev, curr):
    """
    takes function to report or print a gauge
    """
    curr['time'] = now = time.monotonic()
    if prev:
        dt_s = now - prev['time']
    else:
        dt_s = 0
    dt_ms = dt_s * 1000

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
    # I saw Zabbix, and couldn't find anything off the shelf....
    # see full_diskstats() for generation/descriptions

    # https://kernel-internals.org/io/observability/
    # was of initial help understanding how iostat cooks its results

    # FINALLY doing deltas and calculations here because it's too much of a
    # pain in graphite queries (and need to know the reporting interval),
    # trying to stick to what "iostat -x" reports and not make
    # anything up!
    fulldisk = curr['disk'] = full_diskstats()
    prevdisk = prev.get('disk')
    if prevdisk and dt_s:
        for dev, fspath in dev_to_fs.items():
            fsname = DISKS.get(fspath, "")
            if not fsname:
                continue

            stats = fulldisk[dev]
            p = prevdisk[dev]

            def g(op, stat, value):
                if value >= 0:  # defend against wrapping!
                    f(f"disk.nstats.{op}.{stat}.{host}.{fsname}", value)

            pops = p["ops"]
            for op, counts in stats["ops"].items():
                # op is read/write/discard
                # counts is dict with keys complete, merged, kB, ms

                pcounts = pops[op]      # prev counts

                # compute deltas (can be negative if C long wrapped):
                d_count = counts.completed - pcounts.completed
                d_kB = counts.kB - pcounts.kB
                d_ms = counts.ms - pcounts.ms
                d_merged = counts.merged - pcounts.merged

                if d_count:
                    # merged requests not included in completed total:
                    pct_merged = 100 * d_merged / (d_count + d_merged)
                    avg_kB = d_kB / d_count
                    avg_wait_ms = d_ms / d_count
                else:
                    pct_merged = avg_kB = avg_wait_ms = 0

                g(op, "reqs-sec", d_count / dt_s) # requests per second
                g(op, "kb-sec", d_kB / dt_s) # kBbytes per second
                g(op, "avg-wait-ms", avg_wait_ms) # avg wait in ms
                g(op, "avg-kb", avg_kB) # avg request size in kB
                g(op, "pct-merged", pct_merged) # indicates seqential access

            # not operation with full stats, but using same names as above
            # (not available for partitions):
            d_flushes = stats["flush-completed"] - p["flush-completed"]
            d_flush_ms = stats["flush-ms"] - p["flush-ms"]
            g("flush", "reqs-sec", d_flushes / dt_s) # flushes/second
            if d_flushes:
                avg_flush_ms = d_flush_ms / d_flushes
            else:
                avg_flush_ms = 0
            g("flush", "avg-wait-ms", avg_flush_ms) # avg flush wait time

            # remainder not per-operation:
            d_weighted = stats["weighted-time"] - p["weighted-time"]
            d_busy = stats["time-busy"] - p["time-busy"]
            g("overall", "queue-avg-len", d_weighted / dt_ms)
            g("overall", "in-progress", stats["in-progress"]) # instantaneous
            g("overall", "utilization", 100 * d_busy / dt_ms)

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
debug = '--debug' in sys.argv
if debug:
    print(filesystems, dev_to_fs)
while True:
    # PB: why did I put this inside the loop? in case don't start up correctly??
    c = statsd.StatsdClient(STATSD_HOST, 8125, prefix="mc.systems")
    if debug:
        f = print
        INTERVAL = 10
    else:
        f = c.gauge

    curr = {}
    report(f, prev, curr)
    prev = curr

    if debug:
        sys.stdout.flush()
    sleep_sec = INTERVAL - time.time() % INTERVAL
    time.sleep(sleep_sec)
