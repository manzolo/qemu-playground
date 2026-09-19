"""A local, passive screenshot viewer with no accumulating frame archive."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import tempfile

from .core import LabError, open_file
from .screens import capture


def viewer(vm, interval, italian=False):
    labels = dict(
        title=f'{vm} · ' + ('Screenshot in diretta' if italian else 'Live screenshots'),
        hint=(f'Aggiornamento ogni {interval:g} s · Sola lettura · Ctrl-C nel terminale per uscire'
              if italian else f'Updates every {interval:g} s · Read-only · Ctrl-C in the terminal to exit'),
        waiting='In attesa della schermata…' if italian else 'Waiting for a frame…',
        updated='Ultimo aggiornamento: ' if italian else 'Last updated: ',
        stopped='VM spenta · Ultima schermata conservata' if italian else 'VM stopped · Last frame retained',
        closed='Follow terminato · Ultima schermata conservata' if italian else 'Follow ended · Last frame retained',
    )
    return ('''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live screenshots</title><style>
body{margin:0;background:#111827;color:#e5e7eb;font:16px system-ui}
header{padding:16px 24px}h1{font-size:20px;margin:0 0 8px}p{margin:6px 0;color:#a5b4c8}
main{text-align:center;padding:0 16px 16px}img{max-width:100%;max-height:78vh;object-fit:contain}
pre{text-align:left;white-space:pre-wrap;overflow-wrap:anywhere} [hidden]{display:none}
</style></head><body><header><h1></h1><p id="hint"></p><p id="status" role="status"></p></header>
<main><img id="screen" alt="VM screenshot" hidden><pre id="serial" hidden></pre></main>
<script>
const labels = LABELS, interval = INTERVAL;
document.title = labels.title;
document.querySelector('h1').textContent = labels.title;
document.getElementById('hint').textContent = labels.hint;
const status = document.getElementById('status'), screen = document.getElementById('screen');
const serial = document.getElementById('serial');
status.textContent = labels.waiting;
async function refresh() {
  const started = performance.now();
  try {
    const response = await fetch('frame', {cache: 'no-store'});
    if (!response.ok) throw new Error('Viewer unavailable');
    const frame = await response.json();
    if (frame.stopped) { status.textContent = labels.stopped; return; }
    if (frame.image) {
      const next = new Image(); next.src = frame.image; await next.decode();
      screen.src = next.src; screen.hidden = false; serial.hidden = true;
    } else if (frame.text !== undefined) {
      serial.textContent = frame.text; serial.hidden = false; screen.hidden = true;
    }
    status.textContent = frame.error || labels.updated + new Date().toLocaleTimeString();
  } catch (_) { status.textContent = labels.closed; return; }
  setTimeout(refresh, Math.max(0, interval - (performance.now() - started)));
}
refresh();
</script></body></html>'''.replace('LABELS', json.dumps(labels))
            .replace('INTERVAL', str(interval * 1000))).encode()


def follow(lab, *, interval=2, open_browser=True, dry=False):
    if not math.isfinite(interval) or interval <= 0:
        raise LabError('Screenshot interval must be a finite number greater than zero')
    if dry:
        print(f'Follow passive QMP screenshots via {lab.qmp} every {interval:g}s; open={open_browser}')
        return
    if not lab.pid():
        raise LabError('VM is stopped; start or install it before following screenshots')
    lab.ensure()
    page = viewer(lab.vm, interval, lab.cfg.get('LAB_LANG') == 'it')
    route = '/' + secrets.token_urlsafe(24) + '/'
    with tempfile.TemporaryDirectory(prefix='follow-', dir=lab.work) as folder:
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == route:
                    body, mime = page, 'text/html; charset=utf-8'
                elif self.path == route + 'frame':
                    frame = {}
                    if not lab.pid():
                        frame['stopped'] = True
                    else:
                        try:
                            raw = Path(folder) / (secrets.token_hex(12) + '.ppm')
                            payload, suffix = capture(lab, raw)
                            if suffix == '.png':
                                frame['image'] = 'data:image/png;base64,' + base64.b64encode(payload).decode()
                            else:
                                frame['text'] = payload.decode()
                        except (LabError, OSError, ValueError) as error:
                            # A reboot or competing QMP request may briefly fail;
                            # keep the last image and retry at the next interval.
                            frame['error'] = lab.redact(str(error))
                    body, mime = json.dumps(frame).encode(), 'application/json'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            # Finish in-flight captures before deleting their temporary directory.
            server.daemon_threads = False
            url = f'http://127.0.0.1:{server.server_port}{route}'
            print(f'Follow screenshots every {interval:g}s: {url}\n'
                  'Ctrl-C returns; the VM and installer continue.', flush=True)
            if open_browser:
                open_file(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nScreenshot follow stopped. The VM and installer continue.', flush=True)
