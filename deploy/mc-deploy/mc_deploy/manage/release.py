"""
from mediacloud/mc-manage/mc-manage/airtable-release-update.py (v1.1.5)
see README.md for why
"""

import datetime as dt

from pyairtable import Api

# from pyairtable.formulas import match # unused value


def create_release(
    codebase_name: str, version_info: str, api_key: str, base_id: str
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
