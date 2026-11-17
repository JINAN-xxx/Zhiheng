#!/bin/sh
set -eu
umask 077

: "${MINIO_BUCKET:?Set a dedicated business bucket}"
: "${MINIO_APP_ACCESS_KEY:?Set a dedicated application access key}"
: "${MINIO_APP_SECRET_KEY:?Set a dedicated application secret key}"
case "$MINIO_BUCKET" in
  ''|*[!a-z0-9-]*) printf '%s\n' 'Invalid business bucket name' >&2; exit 1 ;;
esac
if [ "$MINIO_APP_ACCESS_KEY" = "$MINIO_ROOT_USER" ]; then
  printf '%s\n' 'Application credentials must be separate from the root account' >&2
  exit 1
fi

mc alias set business http://file-objects:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --region "${MINIO_REGION:-us-east-1}" --ignore-existing "business/$MINIO_BUCKET" >/dev/null
mc anonymous set none "business/$MINIO_BUCKET" >/dev/null
policy="$(mktemp)"
trap 'rm -f "$policy"' EXIT
cat > "$policy" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET/*"]
    }
  ]
}
JSON
mc admin user add business "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY" >/dev/null
mc admin policy create business zhiheng-files-runtime "$policy" >/dev/null
mc admin policy attach business zhiheng-files-runtime --user "$MINIO_APP_ACCESS_KEY" >/dev/null
printf '%s\n' 'Private business bucket and restricted application account are ready.'
