"""Self-contained reports built from evidence, never from a success flag."""
import base64
from datetime import datetime, timezone
from html import escape
from .core import LabError, atomic, records, tail

STYLE = '''body{font:16px/1.6 system-ui,sans-serif;max-width:1080px;margin:40px auto;padding:0 24px;color:#172333;background:#f6f8fb}h1,h2{line-height:1.2}header,article,figure{background:white;padding:24px;border-radius:12px;margin:20px 0;border:1px solid #dae1ea}header{border-top:6px solid #2766bd}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101e30;color:#e4eefb;padding:16px;border-radius:8px;font:13px/1.6 monospace}img{max-width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #dce3ea;vertical-align:top;overflow-wrap:anywhere}code{overflow-wrap:anywhere}.warning{color:#8b3600}.muted{color:#526578}figure{break-inside:avoid}footer{margin-top:32px}@media print{body{margin:0;background:white}pre{color:black;background:#eee}article,figure{border-radius:0}}'''


def report(lab, pdf=False, dry=False):
    output = lab.safe('out', lab.vm + '.html')
    if dry:
        print(f'Generate self-contained HTML: {output}' + (' and optional PDF' if pdf else ''))
        return output
    history = records(lab.work / 'events.jsonl')
    frames = records(lab.work / 'screenshots' / 'timeline.jsonl')
    installations = [e for e in history if e.get('kind') == 'installation']
    verdict = installations[-1].get('outcome', 'unknown') if installations else 'Not yet validated / Non ancora validata'
    assisted = any(e.get('kind') == 'intervention' for e in history)
    if assisted:
        verdict += ' — history includes explicit keyboard intervention'
    if len(installations) > 1:
        verdict += f' — attempt {len(installations)}; previous outcomes retained below'
    def stamp(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')
    def render_frame(frame):
        path = lab.safe('work', lab.vm, 'screenshots', frame['file'])
        caption = escape(stamp(frame['time']) + ' — ' + frame['caption'])
        if not path.is_file():
            body = '<p>Evidence was explicitly removed.</p>'
        elif path.suffix == '.png':
            body = '<img alt="Guest framebuffer" src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '">'
        else:
            body = '<pre>' + escape(lab.redact(path.read_text(errors='replace'))) + '</pre>'
        return f'<figure>{body}<figcaption>{caption}</figcaption></figure>'
    rows = []
    for event in history:
        command = event.get('command', event.get('detail', event.get('kind', '')))
        rows.append('<tr><td>' + escape(stamp(event['time'])) + '</td><td><code>' +
                    escape(lab.redact(str(command))) + '</code></td><td>' +
                    escape(str(event.get('duration', '—'))) + '</td><td>' +
                    escape(str(event.get('outcome', 'started'))) + '</td></tr>')
    latest = render_frame(frames[-1]) if frames else '<p>No screenshot available / Nessuna schermata disponibile.</p>'
    logs = ''.join('<h3>' + name + '</h3><pre>' + escape(lab.redact(tail(lab.work / name))) + '</pre>'
                   for name in ('serial.log', 'steps.log', 'qemu.log'))
    doc = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QEMU playground — {escape(lab.vm)}</title><style>{STYLE}</style>
<header><p class="muted">QEMU PLAYGROUND · {escape(stamp(__import__('time').time()))}</p><h1>{escape(lab.vm)}</h1>
<p><strong>Verdict / Esito: {escape(verdict)}</strong></p><p>Evidence report. A prepared disk or reachable SSH port alone is not proof of a completed installation.</p></header>
<h2>Latest screen / Ultima schermata</h2>{latest}
<article><h2>Commands and outcomes / Comandi ed esiti</h2><table><tr><th>UTC</th><th>Command / detail</th><th>Seconds</th><th>Outcome</th></tr>{''.join(rows)}</table></article>
<h2>Timeline</h2>{''.join(render_frame(f) for f in frames)}
<article><h2>Log tails / Code dei log</h2>{logs}</article>
<article lang="it"><h2>Guida rapida</h2><p>Il menu mostra il comando prima di eseguirlo. Le operazioni lunghe proseguono in background: Ctrl-C chiude la visualizzazione e torna al menu.</p>
<pre>./lab status
./lab ssh {lab.vm} -- {'"ver"' if lab.vm == 'windows-11' else '"uname -a"'}
./lab shot {lab.vm}
./lab stop {lab.vm}
./lab clean {lab.vm} disk seed --dry-run</pre>
<p>Gli screenshot sono passivi. Solo <code>--nudge</code> invia un tasto e rende il giro assistito. In caso di timeout il disco e la VM rimangono disponibili per la diagnosi. Per una nuova installazione, ferma la VM e scegli esplicitamente cosa cancellare. ISO e chiavi condivise sono escluse da <code>clean all</code>.</p></article>
<footer class="muted">No external scripts, fonts or images. Generated from local evidence; logs may contain output of your own guest commands.</footer></html>'''
    atomic(output, doc)
    print(f'Report: {output}')
    if pdf:
        try:
            import markdown
            from weasyprint import HTML
        except (ImportError, OSError) as e:
            raise LabError('HTML saved. PDF needs markdown + weasyprint: sudo apt-get install python3-markdown python3-weasyprint') from e
        HTML(string=doc, base_url=str(output.parent)).write_pdf(output.with_suffix('.pdf'))
        print(f'PDF: {output.with_suffix(".pdf")}')
    return output
