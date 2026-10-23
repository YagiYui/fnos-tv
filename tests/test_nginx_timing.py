"""Run only in a disposable Linux container: real Nginx timing and media-path redaction."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        time.sleep(.08)
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{}')

    do_POST = do_GET


template = Path(sys.argv[1] if len(sys.argv) > 1 else 'config/nginx.conf.template').read_text()
server = ThreadingHTTPServer(('127.0.0.1', 5666), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory() as folder:
    config = Path(folder) / 'nginx.conf'
    config.write_text(template.replace('${FNOS_URL}', 'http://127.0.0.1:5666'))
    subprocess.run(['nginx', '-t', '-c', str(config)], check=True, capture_output=True)
    nginx = subprocess.Popen(['nginx', '-c', str(config), '-g', 'daemon off;'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(40):
            try:
                with socket.create_connection(('127.0.0.1', 80), .2):
                    break
            except OSError:
                time.sleep(.05)
        opener = build_opener(ProxyHandler({}))
        for path, body in [('/fnos/v/api/v1/play/play?redact=fixture-query', b'{}'),
                           ('/fnos/v/media/fixture-session/00000.ts?redact=fixture-query', None),
                           ('/v/media/fixture-session/main.m3u8', None),
                           ('/v/media/fixture-session/unknown-resource', None)]:
            with opener.open(Request('http://127.0.0.1' + path, data=body), timeout=3) as response:
                assert response.status == 200 and response.read() == b'{}'
    finally:
        nginx.terminate()
        output, errors = nginx.communicate(timeout=5)
        server.shutdown()
        server.server_close()
    assert 'fixture-session' not in output and 'fixture-query' not in output, output
    assert '/v/media/[session]/00000.ts' in output
    assert '/v/media/[session]/main.m3u8' in output
    assert '/v/media/[session]/[resource]' in output
    times = re.findall(r'rt=([\d.]+) uct=([\d.]+) uht=([\d.]+) urt=([\d.]+)', output)
    assert len(times) == 4, output
    assert all(float(header) >= .05 and float(total) >= .05 for total, connect, header, upstream in times), times
print('PASS: real Nginx logs measured request/upstream timings while omitting media sessions and queries')
