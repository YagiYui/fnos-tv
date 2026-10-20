# Regression tests

Run from the repository root (Python standard library only):

```sh
python tests/test_backend.py
python tests/test_ass_support.py
python tests/test_manual_danmaku.py
```

`test_external_play.py apps/danmu/external_play.py` additionally requires Flask and requests; run in a disposable Linux test container. It uses a local HTTP fixture.

`test_hardening.py` is a Linux container integration fixture, not a general test discovery target. Its argument must point to a staging directory containing `ass_support.py` and `cache_maintenance.py`; the container must have the secure entrypoint, rendered Nginx configuration, private tmpfs, and a pre-created `/app/data/private-sentinel` with mode 600. Ports 80/8000/8001 must be free. Never run it in the production container: it starts fixture servers and Nginx and exercises cache maintenance.
