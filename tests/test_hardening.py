import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

base=Path(sys.argv[1])
ass=load('hardened_ass',base/'ass_support.py')
assert stat.S_IMODE(Path('/run/fnos-tv-private').stat().st_mode)==0o700
assert stat.S_IMODE(Path('/app/data/private-sentinel').stat().st_mode)==0o600, 'Entrypoint widened existing data permissions'
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    keys=list(pool.map(lambda _:ass.secret_key(),range(24)))
assert len(set(keys))==1 and len(keys[0])==32
assert stat.S_IMODE((ass.PRIVATE/'subtitle-key').stat().st_mode)==0o600

# Exercise the existing real cross-process scheduler lock, with harmless sleeping child processes.
with tempfile.TemporaryDirectory() as temp:
    ass.CACHE=Path(temp);children=[];original_popen=ass.subprocess.Popen
    def fake_popen(*args,**kwargs):
        process=original_popen([sys.executable,'-c','import time; time.sleep(10) # extraction_job'])
        children.append(process);return process
    ass.subprocess.Popen=fake_popen
    def submit(index):
        folder=ass.CACHE/str(index);folder.mkdir()
        try:ass.ensure_extraction('fixture','0:0',folder)
        except ass.SubtitlePending:pass
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(submit,range(6)))
        assert len(children)==2, f'Expected two globally limited jobs, got {len(children)}'
    finally:
        ass.subprocess.Popen=original_popen
        for process in children:process.terminate()
        for process in children:process.wait()

maintenance=load('hardened_cache',base/'cache_maintenance.py')
maintenance.job_running=lambda state:bool(state.get('active'))
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)/'cache';root.mkdir()
    old=root/('a'*64);old.mkdir();(old/'subtitle.ass').write_bytes(b'a'*20)
    active=root/('b'*64);active.mkdir();(active/'job.json').write_text(json.dumps({'active':True}))
    fresh=root/('c'*64);fresh.mkdir();(fresh/'subtitle.sup').write_bytes(b'PG'+b'a'*20)
    unknown=root/'manual-validation';unknown.mkdir();(unknown/'keep').write_bytes(b'keep')
    now=time.time();past=now-40*86400
    for directory in [old,active]:os.utime(directory,(past,past))
    result=maintenance.prune(root,now=now)
    assert result['removed']==1 and not old.exists() and active.exists() and fresh.exists() and unknown.exists()
# Verify the real Nginx prefix/exact routing, not only nginx -t and Flask test clients.
class Main(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):self.send_response(401);self.end_headers()
class Stream(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.send_response(403);self.end_headers()
servers=[ThreadingHTTPServer(('127.0.0.1',8000),Main),ThreadingHTTPServer(('127.0.0.1',8001),Stream)]
for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
nginx=subprocess.Popen(['nginx','-g','daemon off;'])
try:
    for _ in range(30):
        try:
            with socket.create_connection(('127.0.0.1',80),.2):break
        except OSError:time.sleep(.1)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for req,status in [(urllib.request.Request('http://127.0.0.1/danmu/external-play',data=b'{}'),401),
                       (urllib.request.Request('http://127.0.0.1/danmu/external-play/'+'z'*43+'/preset.m3u8'),403)]:
        try:opener.open(req,timeout=3);raise AssertionError('Auth guard missing')
        except urllib.error.HTTPError as error:assert error.code==status,error.code
finally:
    nginx.terminate();nginx.wait()
    for server in servers:server.shutdown();server.server_close()
print('PASS: secure startup, private keys, scheduler=2, cache retention, real Nginx POST and streaming routing without redirects')
