#!/bin/sh
# script to create BackBlaze B2 buckets & keys
# Phil Budne, May 2024
#
# requires "pip install b2[full]"
#
# needs "mediacloud" key (or master key) in B2_APPLICATION_KEY[_ID] env vars
#	(writes ~/.config/b2/account_info (SQLite3 db file))
# or creds from previous run in above file

# create output/temp files without group & world access:
umask 077

KEYS_FILE=bucket-keys
KEY_RO_SUFFIX=-ro
KEY_RW_SUFFIX=-rw

for DIR in $(echo $PATH | sed 's/:/ /'); do
    if [ -x $DIR/b2 ]; then
	FOUND=$DIR
	break
    fi
done
if [ "x$FOUND" = x ]; then
    echo 'could not find b2 command' 1>&2
    exit 1
fi

usage() {
    echo "Usage: $0 create [--public|-p|--ro-key|-r|--help|-h] BUCKET-NAMES..." 1>&2
    echo "	$0 delete BUCKET-NAMES..." 1>&2
    exit 255
}

COMMAND=$1
if [ "x$COMMAND" = x ]; then
    usage
fi
shift

case $COMMAND in
create)
    PUBPRIV=allPrivate
    MAKE_RO_KEY=
    while [ $# -gt 0 ]; do
	# Update usage function when this is changed!!!!!
	case "$1" in
	--debug|-d) set -x; DEBUG=1;;
	--public|-p) PUBPRIV=allPublic;;
	--ro-key|-r) MAKE_RO_KEY=1;;
	--help|-h) usage;;
	-*) echo "create: unknown option $1" 1>&2; usage;;
	*) break;
	esac
	shift
    done
    if [ "x$*" = x ]; then
	usage
    fi

    if [ -f $KEYS_FILE -a ! -w $KEYS_FILE ]; then
	echo "$KEYS_FILE exists but not writable" 1>&2
	exit 1
    fi

    TMP=./temp$$
    trap "rm -f $TMP" 0
    if ! b2 list-keys > $TMP; then
	echo "list-keys failed" 1>&2
	exit 1
    fi

    # initial loop to validate all bucket names
    echo "checking bucket and keys..." 1>&2
    for BUCKET in "$@"; do
	# must have at LEAST one character after mediacloud-
	case "$BUCKET" in
	'')
	    echo "create: empty bucket name" 1>&2
	    usage
	    ;;
	*-)
	    echo "create: please don't end bucket name $BUCKET with hyphen" 1>&2
	    exit 2
	    ;;
	*[^a-z0-9-]*)
	    echo "create: please use only lower-case, digits and hyphen in bucket name $BUCKET" 1>&2
	    exit 2
	    ;;
	mediacloud-?*)
	    ;;
	*)
	    echo "create: bucket name $BUCKET does not start with mediacloud-" 1>&2
	    exit 3
	    ;;
	esac

	if b2 ls $BUCKET > /dev/null 2>&1; then
	    echo "bucket $BUCKET exists" 1>&2
	    exit 1
	fi

	if ! awk "\$2 == \"$RO_KEY_NAME\" || \$2 == \"$RW_KEY_NAME\" { print \"found key\", \$2; CODE=1 } END { exit CODE; }" < $TMP 1>&2; then
	    exit 1
	fi
    done

    if [ "x$DEBUG" != x ]; then
	echo debug: exiting
	exit 33
    fi

    for BUCKET in "$@"; do
	RO_KEY_NAME=${BUCKET}${KEY_RO_SUFFIX}
	RW_KEY_NAME=${BUCKET}${KEY_RW_SUFFIX}

	# b2 create-bucket [-h] [--bucket-info BUCKET_INFO] [--cors-rules CORS_RULES]
	#                   [--file-lock-enabled] [--replication REPLICATION]
	#                   [--default-server-side-encryption {SSE-B2,none}]
	#                   [--default-server-side-encryption-algorithm {AES256}]
	#                   [--lifecycle-rule LIFECYCLE_RULES | --lifecycle-rules LIFECYCLE_RULES]
	#                   bucketName {allPublic,allPrivate}
	BUCKET_ID=$(b2 create-bucket $BUCKET $PUBPRIV)
	if [ $? != 0 ]; then
	    echo "b2 create-bucket failed" 1>&2
	    exit 1
	fi
	echo "bucket id $BUCKET_ID"

	# other create-key options:
	# [--name-prefix NAME_PREFIX]
	# [--duration DURATION]

	# initial lists from keys created using webUI (FOR REFERENCE, DO NOT EDIT):
	#RO_CAPS="listBuckets,listFiles,readBucketEncryption,readBucketNotifications,readBucketReplications,readBuckets,readFiles,shareFiles"
	#RW_CAPS="$RO_CAPS,writeFiles,deleteFiles,writeBucketEncryption,writeBucketReplications,writeBucketNotifications"

	# add new capabilites here only:
	RO_CAPS="listBuckets,listFiles,readBucketEncryption,readBucketNotifications,readBucketReplications,readBuckets,readFiles,shareFiles"
	RW_CAPS="$RO_CAPS,writeFiles,deleteFiles,writeBucketEncryption,writeBucketReplications,writeBucketNotifications"

	if [ "x$MAKE_RO_KEY" != x ]; then
	    RO_KEYS=$(b2 create-key --bucket $BUCKET $RO_KEY_NAME $RO_CAPS)
	    if [ $? != 0 ]; then
		echo "failed to create ${BUCKET}-ro key" 1>&2
		# XXX remove bucket?
		exit 1
	    fi
	    echo $RO_KEY_NAME $RO_KEYS >> $KEYS_FILE
	    chmod 700 $KEYS_FILE
	fi
	RW_KEYS=$(b2 create-key --bucket $BUCKET $RW_KEY_NAME $RW_CAPS)
	if [ $? != 0 ]; then
	    echo "failed to create ${BUCKET}-rw" 1>&2
	    # XXX remove bucket & -ro key?
	    exit 1
	fi
	echo $RW_KEY_NAME $RW_KEYS >> $KEYS_FILE
	chmod 700 $KEYS_FILE
    done
    ;;

delete)
    # handle options (if any) here

    if [ "x$*" = x ]; then
	usage
    fi
    for BUCKET in "$@"; do
	BUCKET=$1

	if [ "x$BUCKET" = x  ]; then
	    usage
	fi

	RO_KEY_NAME=${BUCKET}${KEY_RO_SUFFIX}
	RW_KEY_NAME=${BUCKET}${KEY_RW_SUFFIX}

	if ! b2 delete-bucket $BUCKET; then
	    echo b2 delete-bucket $BUCKET failed 1>&2
	    exit 1
	fi
	for KEY in $RO_KEY_NAME $RW_KEY_NAME; do
	    if ! b2 delete-key $KEY; then
		echo b2 delete-key $KEY failed 1>&2
		# NOTE: continue regardless
	    fi
	done
    done
    ;;

*)
    usage
    ;;
esac
