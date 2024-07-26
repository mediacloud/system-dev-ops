#!/bin/sh
# Phil Budne, November 2023
# Create or Delete an S3 Bucket, IAM Policy, User & Keys for access
# XXX have option to NOT create user/keys??

umask 077

# requires Ubuntu "awscli" and "jq" packages
if [ ! -f /usr/bin/aws ]; then
    echo cannot find awscli 1>&2
    exit 1
fi

if [ ! -f /usr/bin/jq ]; then
    echo cannot find jq 1>&2
    exit 1
fi

################################ create function

LSTMP=/tmp/make-s3-bucket-ls$$
POLICYTMP=/tmp/make-s3-bucket-policy$$.json

# XXX maybe remove bucket, user, policy on failure??
trap "rm -f $LSTMP $POLICYTMP" 0

create() {
    ################ 1. create bucket
    if aws s3 ls > $LSTMP; then
	if grep " $BUCKET\$" $LSTMP; then
	    echo bucket $BUCKET exists 1>&2
	    exit 3
	fi
    else
	echo "aws s3 ls failed" 1>&2
	exit 4
    fi

    if ! aws s3 mb s3://$BUCKET; then
	echo "aws s3 make-bucket failed" 1>&2
	exit 5
    fi

    ################ 2. create r/w policy

    echo create policy $POLICY

    # note does not have "ListAllMyBuckets" permission
    cat <<EOF > $POLICYTMP
{
    "Version": "2012-10-17",
    "Statement": [
	{
	    "Effect": "Allow",
	    "Action": [
		"s3:ListBucket"
	    ],
	    "Resource": [
		"arn:aws:s3:::$BUCKET"
	    ]
	},
	{
	    "Effect": "Allow",
	    "Action": [
		"s3:DeleteObject",
		"s3:GetObject",
		"s3:PutObject"
	    ],
	    "Resource": [
		"arn:aws:s3:::$BUCKET/*"
	    ]
	}
    ]
}
EOF

    # XXX test if exists first??
    # XXX capture stdout, grab Policy.Arn w/ jquery??
    aws iam create-policy --policy-name $POLICY \
	--description "read-write access to $BUCKET s3 bucket"  \
	--policy-document file://$POLICYTMP
    STATUS=$?
    if [ $STATUS != 0 ]; then
	echo create-policy $POLICY failed 1>&2
	exit 6
    fi

    ################ 3. create user w/ same name as policy

    echo create user $UNAME
    if ! aws iam create-user --user-name $UNAME; then
	echo iam create-user failed 1>&2
	exit 7
    fi

    ################ 4. attach policy to user

    echo attach policy $POLICY to user $UNAME
    if ! aws iam attach-user-policy --user-name $UNAME --policy-arn $POLARN; then
	echo iam create-user failed 1>&2
	exit 7
    fi

    ################ 5. create keys for user

    KEYS=${BUCKET}-keys.json
    if aws iam create-access-key --user-name $UNAME > $KEYS; then
	chmod 400 $KEYS
	echo keys in $KEYS
    else
	echo create-access-key failed 1>&2
	exit 8
    fi
} # create

################################ delete function

# also use for "create" error cleanup??

delete() {
    echo remove bucket
    if ! aws s3 rb s3://$BUCKET; then
	echo "aws s3 rb failed" 1>&2
    fi

    echo detach policy $POLICY from user $UNAME
    if ! aws iam detach-user-policy --user-name $UNAME --policy-arn $POLARN; then
	echo iam detach-user-policy failed 1>&2
    fi

    echo delete policy $POLICY
    if ! aws iam delete-policy --policy-arn $POLARN; then
	echo iam delete-policy failed 1>&2
    fi

    AKTMP=/tmp/s3-bucket$$.json
    trap "rm -f $AKTMP" 0

    echo list-access-keys for user $UNAME
    if ! aws iam list-access-keys --user-name $UNAME > $AKTMP; then
	echo iam list-access-keys failed 1>&2
    fi

    for AK in $(jq '.AccessKeyMetadata[].AccessKeyId' < $AKTMP | sed 's/"//g' ); do
	if ! aws iam delete-access-key --user-name $UNAME --access-key-id $AK; then
	    echo could not delete access-key-id $AK 1>&2
	    # delete-user will probably fail
	fi
    done

    echo delete user
    if ! aws iam delete-user --user-name $UNAME; then
	echo iam delete-user failed 1>&2
    fi

} # delete

################################################################ main

usage() {
    echo "Usage: $0 create|delete BUCKET" 1>&2
    exit 255
}

COMMAND=$1
if [ "x$COMMAND" = x ]; then
    echo need COMMAND 1>&2
    usage
fi

BUCKET=$2
if [ "x$BUCKET" = x ]; then
    echo need BUCKET name 1>&2
    usage
fi

if echo $BUCKET | grep '^mediacloud' >/dev/null; then
    if echo $BUCKET | grep '[^a-z0-9-]' >/dev/null; then
	echo only user a-z 0-9 and - in bucket names 1>&2
	exit 2
    fi
else
    echo bucket name does not start with mediacloud 1>&2
    exit 2
fi

POLICY=${BUCKET}-rw

POLARN=arn:aws:iam::441579791897:policy/$POLICY
UNAME=$POLICY

case $COMMAND in
    create) create;;
    delete) delete;;
    *) usage;;
esac
