#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /app/data/log
# /run is a container tmpfs, outside inherited NAS ACLs and persistent backups.
mkdir -p /run/fnos-tv-private
chmod 700 /run/fnos-tv-private
test "$(stat -c %a /run/fnos-tv-private)" = 700
envsubst '${FNOS_URL}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
# A release must never download unpaired upstream code over its mounted frontend.
if [ "${RUN_AND_UPDATE_WEB:-false}" = "true" ]; then
    echo 'Runtime frontend updates are disabled for the customized release.' >&2
fi
exec "$@"
