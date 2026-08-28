"""
from mediacloud/mc-manage/mc-manage/airtable-release-update.py (v1.1.5)
see README.md for why
"""

import datetime as dt

from pyairtable import Api

# from pyairtable.formulas import match # unused value


def create_release(
    *, codebase_name: str, version_info: str, api_key: str, base_id: str
) -> None:
    """
    report package updates to a central airtable repository
    """

    api = Api(api_key)
    info = "Automatic deployment record"

    # Get codebases id:
    codebase_table = api.table(base_id, table_name="Software - Codebases")
    # not used:
    # codebase_res = codebase_table.first(formula=match({"Name": codebase_name}))

    # Get the current time in UTC
    iso_timestamp = dt.datetime.utcnow().isoformat()

    resp = codebase_table.batch_upsert(
        [
            {
                "fields": {
                    "Name": codebase_name,
                    "Version": version_info,
                    "Release Time": iso_timestamp,
                    "Info": info,
                }
            }
        ],
        key_fields=["Name"],
    )
    print(resp)


# ~/github/mc-manage/mc-manage/airtable-release-update.py
# to replace remaining uses of mc-manage
if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="A utility for updating an airtable package release record"
    )

    parser.add_argument(
        "--name", help="additional deployment name", required=True
    )
    parser.add_argument(
        "--version", help="A descriptive version string", required=True
    )

    args = parser.parse_args()

    create_release(
        codebase_name=args.name,
        version_info=args.version,
        api_key=os.environ["AIRTABLE_API_KEY"],
        base_id=os.environ["MEAG_BASE_ID"],
    )
