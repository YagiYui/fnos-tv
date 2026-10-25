#!/usr/bin/env bash
set -euo pipefail
# Only disposable container filesystems and loopback listeners are used.
image=${1:?Supply the locally built image}
docker run --rm --network none --entrypoint /bin/bash \
  --tmpfs /run/fnos-tv-private:rw,noexec,nosuid,size=64m,mode=0700 \
  -e FNOS_URL=http://127.0.0.1:5666 "$image" -ec '
    mkdir -p /app/data
    touch /app/data/private-sentinel
    chmod 600 /app/data/private-sentinel
    /entrypoint.sh /bin/true
    python tests/test_backend.py
    python tests/test_ass_support.py
    python tests/test_manual_danmaku.py
    python tests/test_routes.py
    python tests/test_migrations.py
    python tests/test_release.py
    python tests/test_external_play.py apps/danmu/external_play.py
    mkdir -p /tmp/hardening
    cp apps/danmu/ass_support.py cache_maintenance.py /tmp/hardening/
    python tests/test_hardening.py /tmp/hardening
    python -c "import app"
    nginx -t
  '
