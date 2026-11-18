#!/bin/bash
# One-shot MinIO setup (run by the minio-init service): the archive bucket, the least-privilege
# `archiver` user (put/get/list on that bucket only, no delete; D-ARC-09) and the get-only `replay`
# user the API reads the archive with (get/list, T4-l). `replay` keeps s3:ListBucket on purpose: without
# it S3 answers a missing key 403 AccessDenied, and the replay reader needs the 404 NoSuchKey to treat
# a gone object as ArchiveCorrupt (409) rather than a permission fault. Idempotent: safe to re-run.
set -euo pipefail
export MC_CONFIG_DIR="${MC_CONFIG_DIR:-/tmp/mc}"

# Bitnami's setup runs a temporary MinIO that passes the health check, stops it, then starts the real
# one; connecting in that gap is refused. 30 tries x 1 s.
for _ in $(seq 30); do
  if mc alias set s3 http://minio:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY"; then
    ready=1
    break
  fi
  sleep 1
done
[ "${ready:-0}" = 1 ] || exit 1

mc mb --ignore-existing "s3/$S3_BUCKET"
mc admin user add s3 archiver "$S3_ARCHIVER_SECRET"

cat > /tmp/nyx-archiver.json <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": ["arn:aws:s3:::$S3_BUCKET/*"]},
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::$S3_BUCKET"]}
  ]
}
POLICY
mc admin policy create s3 nyx-archiver /tmp/nyx-archiver.json
# capture first: `cmd | grep -q` can die of SIGPIPE under pipefail
entities=$(mc admin policy entities s3 --user archiver)
if [[ $entities != *nyx-archiver* ]]; then
  mc admin policy attach s3 nyx-archiver --user archiver
fi

mc admin user add s3 replay "$S3_REPLAY_SECRET"
cat > /tmp/nyx-replay.json <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": ["arn:aws:s3:::$S3_BUCKET/*"]},
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::$S3_BUCKET"]}
  ]
}
POLICY
mc admin policy create s3 nyx-replay /tmp/nyx-replay.json
entities=$(mc admin policy entities s3 --user replay)
if [[ $entities != *nyx-replay* ]]; then
  mc admin policy attach s3 nyx-replay --user replay
fi
