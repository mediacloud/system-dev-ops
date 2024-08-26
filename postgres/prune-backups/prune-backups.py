"""
Prune S3/B2 dokku postgres backup files using AWS Boto S3 API

Requires "pip install boto3"

Will use keys from AWS_SHARED_CREDENTIALS_FILE (default ~/.aws/credentials):
https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html#shared-credentials-file

Should use any of:
dokku postgres backup config

AWS_PROFILE
AWS_SHARED_CREDENTIALS_FILE

AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
"""

# guides:
# https://github.com/agerwick/backup_retention/blob/main/backup_retention.py
# https://github.com/xolox/python-rotate-backups/blob/master/rotate_backups/__init__.py

import argparse
import collections
import datetime
import os
import re
import sys
from typing import NamedTuple

import boto3
from botocore.config import Config

DEFAULT_SVC = "s3"
SVC2URL = {
    "s3": "https://s3.{region}.amazonaws.com",
    "b2": "https://s3.{region}.backblazeb2.com",
}

# defaults: NOTE! negative values here, or on command line mean "keep them all"
# per rahul: https://github.com/mediacloud/story-indexer/issues/291#issuecomment-2130104544
YEARLY = -1                # unlimited
MONTHLY = 11               # in addition to -01-01 (rahul suggested 6)
WEEKLY = 8                 # sunday backups
DAILY = 30

DATE_RE = re.compile("(\d\d\d\d)-(\d\d)-(\d\d)-(\d\d)-(\d\d)-(\d\d)")

# directory with database subdirs
DOKKU_PG_DIR = "/var/lib/dokku/services/postgres"

# note: tuple, immutable and hashable
class Item(NamedTuple):
    date_time: str              # for sort
    year: str
    month: str
    day: str                    # day of month
    key: str                    # key (object name)
    week: int                   # week of year 0..52

def main():
    ap = argparse.ArgumentParser("s3-prune", "prune backup files on S3-like services")

    # auth/profile related:
    x = os.environ.get("AWS_PROFILE", "default")
    ap.add_argument("--delete", default=False, action="store_true",
                    help="actually delete objects")

    ap.add_argument("--profile", "-P", type=str, default=x,
                    help=f"select aws profile name (default {x})")

    x = os.environ.get("AWS_REGION", None)
    ap.add_argument("--region", "-R", type=str, default=x,
                    help=f"select service region (default {x})")
    ap.add_argument("--service", "-S", type=str, default=DEFAULT_SVC,
                    choices=SVC2URL.keys(),
                    help=f"select storage service (default {DEFAULT_SVC})")

    # grab AWS config from Dokku service directory
    # (mutually exclusive with --profile, --region, --service).
    # Could use contents of DOKKU_PG_DIR for choices.
    # Avoids needing to duplicate sensitive config.
    ap.add_argument("--database", type=str, default=None,
                    help="name of Dokku database to grab service info from")

    # file retention related:
    ap.add_argument("--yearly", "-Y", type=int, default=YEARLY,
                    help=f"number of yearly (YYYY-01-01) backups to keep (default: {YEARLY})")
    ap.add_argument("--monthly", "-M", type=int, default=MONTHLY,
                    help=f"number of additional monthly (YYYY-MM-01) backups to keep (default: {MONTHLY})")
    ap.add_argument("--weekly", "-W", type=int, default=WEEKLY,
                    help=f"number of additional sunday backups to keep (default: {WEEKLY})")
    ap.add_argument("--daily", "-D", type=int, default=DAILY,
                    help=f"number of additional daily backups to keep (default: {DAILY})")

    ap.add_argument("bucket",
                    nargs="?",
                    help=f"BUCKET[/PREFIX]")

    args = ap.parse_args()

    if args.database:
        def read_db_file(fname: str) -> str:
            path = os.path.join(DOKKU_PG_DIR, args.database, "backup", fname)
            with open(path) as f:
                return f.readline().rstrip()

        endpoint_url = read_db_file("ENDPOINT_URL")

        client_args = {
            "aws_access_key_id": read_db_file("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": read_db_file("AWS_SECRET_ACCESS_KEY")
        }
    else:
        if args.profile:
            os.environ["AWS_PROFILE"] = args.profile

        if not args.region:
            ap.error("need either --region or --database")

        endpoint_url = SVC2URL[args.service].format(region=args.region)
        client_args = {}

    s3 = boto3.client("s3", endpoint_url=endpoint_url, **client_args)

    if args.bucket:
        match args.bucket.split('/', 1):
            case [bucket, prefix]:
                pass
            case [bucket]:
                prefix = ""
    elif args.database:
        d = args.database
        if d.endswith("-db"):
            d = d[:-3]
        if not d:
            ap.error("bad database name")
            ap.exit(1)
        bucket = f"mediacloud-{d}-backup"
        prefix = ""
    else:
        # XXX some ap method?
        ap.error("bucket required without --database")
        ap.exit(1)

    dry_run = not args.delete
    print("bucket", bucket)
    print("prefix", prefix or "(none)")
    items = []
    marker = ""

    # keys are returned in lexicographic order, lowest to highest (oldest first)

    while True:
        res = s3.list_objects(Bucket=bucket, Prefix=prefix, Marker=marker)
        if "Contents" not in res:
            break
        for item in res["Contents"]:
            key = item["Key"]

            # XXX check for postgres-DATABASENAME-?
            # (old rss-fetcher dumps would be killed, since db name changed)
            if not key.startswith("postgres"):
                continue

            m = DATE_RE.search(key)
            if not m:
                print("no date:", key)
                continue

            year = m.group(1)
            month = m.group(2)
            day = m.group(3)
            d = datetime.date(int(year), int(month), int(day))
            week = d.timetuple().tm_yday // 7 # week of year

            item = Item(date_time=m.group(0), year=year, month=month, day=day, key=key, week=week)
            items.append(item)

        if not res["IsTruncated"]:
            break
        marker = key  # see https://github.com/boto/boto3/issues/470
        if not marker:
            break

    # ordered from smallest to largest period.
    # MUST have an integer attribute in ap.args!!!
    frequencies = ('daily', 'weekly', 'monthly', 'yearly')

    # thanks to https://github.com/xolox/python-rotate-backups/blob/master/rotate_backups/__init__.py
    groupings = {freq: collections.defaultdict(list) for freq in frequencies}

    def add_to_grouping(freq: str, index: tuple, item: Item, max_to_keep: int | None = None):
        #print("add_to_grouping", freq, index, item.key)
        if max_to_keep and len(groupings[freq][index]) == max_to_keep:
            print("duplicate", freq, item.key, "have", groupings[freq][index][0].key)
            return
        groupings[freq][index].append(item)

    # avoid stale values due to lack of block scoping
    del year
    del month
    del day
    del d
    del week

    # process newest first; keep only last backup each day.
    # if database name changed, items may not be in chronological order, so need sort.
    sorted_items = sorted(items, reverse=True)

    #if sorted_items:
    #    oldest = sorted_items.pop()
    #    print("keeping oldest", oldest.key)

    for item in sorted_items:
        year = item.year
        month = item.month
        day = item.day
        week = item.week
        add_to_grouping('daily', (year, month, day), item, 1)
        add_to_grouping('weekly', (year, week), item)
        add_to_grouping('monthly', (year, month), item)
        add_to_grouping('yearly', (year,), item)

    # avoid stale values due to lack of block scoping
    del year
    del month
    del day
    del week

    kept: set[Item] = set()
    def mark(freq: str, count: int):
        """
        mark `count` newest previously unkept entries
        for frequency `freq` to be kept
        """
        print("mark", freq, count)

        if count == 0:
            return

        # dict entries created newest to oldest
        sorted_groupings = groupings[freq].items()

        for groupid, items in sorted_groupings:
            # keep newest item in grouping
            # if already kept, will move on to next oldest grouping
            if items:
                item = items[0]
                if item not in kept:
                    kept.add(item)
                    print("keeping", freq, item.key)
                    if count > 0: # -1 means infinite
                        count -= 1
                        if count == 0:
                            return
        if count > 0:
            print("could keep", count, "more", freq)
    # end of mark function

    for freq in frequencies:
        mark(freq, getattr(args, freq))

    delete = []                 # keys
    for item in items:
        if item in kept:
            continue
        print("delete", item.key)
        delete.append(item.key)
    print("keep", len(kept), "delete", len(delete))

    if args.delete:
        # S3 has delete_objects call for batch remove
        # (up to 1000 objects per call), but in practice,
        # this should be run daily, and in production
        # should remove at most one file a day.
        print(dir(s3))

if __name__ == '__main__':
    main()
