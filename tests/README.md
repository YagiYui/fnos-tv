# Regression tests

Run from the repository root (Python standard library only):

```sh
python tests/test_backend.py
python tests/test_ass_support.py
python tests/test_manual_danmaku.py
```

`test_external_play.py apps/danmu/external_play.py` additionally requires Flask and requests; run in a disposable Linux test container. It uses a local HTTP fixture.

`python tests/test_routes.py` requires Flask, requests and SQLAlchemy. It loads the actual route handlers against an in-memory SQLite database and mocks only the official service and optional NAS integration; it never imports the production Flask app or reads account configuration.

`python tests/test_migrations.py` requires SQLAlchemy and Alembic. It upgrades temporary fresh and legacy databases, checking that saved records and configuration survive. Run this before any migration deployment.

For the complete disposable Linux runtime fixture, build the paired image and run `bash scripts/test_image.sh <image>`. See `deploy/README.md`; never substitute the production container for this fixture.

`test_hardening.py` is a Linux container integration fixture, not a general test discovery target. Its argument must point to a staging directory containing `ass_support.py` and `cache_maintenance.py`; the container must have the secure entrypoint, rendered Nginx configuration, private tmpfs, and a pre-created `/app/data/private-sentinel` with mode 600. Ports 80/8000/8001 must be free. Never run it in the production container: it starts fixture servers and Nginx and exercises cache maintenance.
