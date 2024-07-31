#!/bin/sh

# output tab delimited bucket name, type, file count, total size
# requires "pip install b2[full]" and "apt install jq"

for BUCKET in $(b2 list-buckets --json | jq '.[].bucketName' | tr -d '"'); do
    b2 get-bucket --show-size "$BUCKET" | jq -r '. | [.bucketName, .bucketType, .fileCount, .totalSize] | @tsv'
done
