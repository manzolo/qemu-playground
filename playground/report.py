"""Self-contained reports built from evidence, never from a success flag."""
import base64
from datetime import datetime, timezone
from html import escape
import re
from .core import LabError, atomic, open_file, records, tail

STYLE = '''
body{font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#172333;background:#f6f8fb}
h1,h2,h3{line-height:1.25}h2{margin:28px 0 12px;font-size:20px}h3{font-size:14px;margin:18px 0 6px;color:#526578}
header,article{background:#fff;padding:22px;border-radius:10px;margin:18px 0;border:1px solid #dae1ea}
header{border-top:5px solid #2766bd}
pre{white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4;background:#101e30;color:#e4eefb;padding:16px;border-radius:6px;font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace;font-variant-ligatures:none}
table{border-collapse:collapse;width:100%;font-size:13px;table-layout:fixed}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid #e2e8ef;vertical-align:top}
th{font-weight:600;color:#526578;white-space:nowrap}
/* Only the command column may break mid-word: letting every cell do it turned
   "Seconds" into a four-line column and shredded the timestamps. */
td.t,td.s{white-space:nowrap;font-variant-numeric:tabular-nums;color:#526578}
td.c code{overflow-wrap:anywhere;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
td.o{overflow-wrap:break-word}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.grid figure{margin:0;background:#fff;border:1px solid #dae1ea;border-radius:8px;padding:10px}
figure{margin:18px 0;background:#fff;border:1px solid #dae1ea;border-radius:10px;padding:16px;break-inside:avoid}
figure img{width:100%;height:auto;display:block;border-radius:4px;background:#000}
.screen-zoom{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;border-radius:4px}
.screen-zoom:focus-visible{outline:3px solid #2766bd;outline-offset:4px}
.zoom-hint{display:block;margin-top:4px;color:#2766bd}
#screen-viewer{width:calc(100vw - 40px);height:calc(100vh - 40px);max-width:none;max-height:none;box-sizing:border-box;padding:0;border:1px solid #526578;border-radius:12px;background:#101e30;color:#e4eefb}
#screen-viewer[open]{display:flex;flex-direction:column}
#screen-viewer::backdrop{background:rgb(5 12 22 / 85%)}
.viewer-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;padding:12px;border-bottom:1px solid #526578}
.viewer-toolbar button{font:inherit;color:inherit;background:#243c58;border:1px solid #526578;border-radius:6px;padding:6px 12px;cursor:pointer}
.viewer-toolbar button:focus-visible{outline:2px solid #90c4ff;outline-offset:2px}
.viewer-toolbar button:disabled{opacity:.4;cursor:default}
#viewer-title{margin-right:auto;font-weight:600}
#viewer-scale{min-width:4em;text-align:center;font-variant-numeric:tabular-nums}
.viewer-viewport{flex:1;min-height:0;overflow:auto;padding:12px}
#viewer-image{display:block;max-width:none;margin:auto;height:auto}
#viewer-caption{padding:8px 12px;margin:0;font-size:12px;overflow-wrap:anywhere}
figure pre{max-height:22em;overflow:hidden}
figcaption{font-size:12px;color:#526578;margin-top:8px;overflow-wrap:anywhere}
.verdict{font-size:17px;font-weight:600}.warning{color:#8b3600}.muted{color:#526578}
footer{margin-top:28px;font-size:12px}
@media print{
  body{margin:0;max-width:none;background:#fff;font-size:11px}
  header,article{border-radius:0;box-shadow:none}
  .grid{grid-template-columns:repeat(3,1fr);gap:8px}
  .grid figure{padding:5px}figcaption{font-size:9px}
  pre{color:#111;background:#eee}table{font-size:10px}
  #screen-viewer,#screen-viewer[open],.zoom-hint{display:none}
}
'''


VIEWER_SCRIPT = '''
<script>
(() => {
  const dialog = document.getElementById('screen-viewer');
  const viewport = dialog.querySelector('.viewer-viewport');
  const image = document.getElementById('viewer-image');
  const caption = document.getElementById('viewer-caption');
  const percent = document.getElementById('viewer-scale');
  const minus = document.getElementById('viewer-minus');
  const plus = document.getElementById('viewer-plus');
  let scale = 1;
  function resize(next) {
    scale = Math.max(.05, Math.min(4, next));
    image.style.width = Math.round(image.naturalWidth * scale) + 'px';
    percent.textContent = Math.round(scale * 100) + '%';
    minus.disabled = scale <= .05;
    plus.disabled = scale >= 4;
  }
  function fit() {
    resize(Math.min(1, (viewport.clientWidth - 24) / image.naturalWidth,
                       (viewport.clientHeight - 24) / image.naturalHeight));
    viewport.scrollTo(0, 0);
  }
  document.querySelectorAll('.screen-zoom').forEach(button => {
    button.addEventListener('click', () => {
      const thumbnail = button.querySelector('img');
      image.onload = fit;
      image.src = thumbnail.src;
      image.alt = thumbnail.alt;
      caption.textContent = button.closest('figure').querySelector('.frame-caption').textContent;
      dialog.showModal();
      if (image.complete && image.naturalWidth) fit();
    });
  });
  minus.addEventListener('click', () => resize(scale / 1.25));
  plus.addEventListener('click', () => resize(scale * 1.25));
  document.getElementById('viewer-fit').addEventListener('click', fit);
  document.getElementById('viewer-actual').addEventListener('click', () => resize(1));
  document.getElementById('viewer-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
})();
</script>
'''


HEADINGS = {
    'en': dict(verdict='Verdict', latest='Latest screen', commands='Commands and outcomes',
               utc='UTC', command='Command / detail', secs='Seconds', outcome='Outcome',
               timeline='Timeline', logs='Log tails', none='No screenshot available.',
               zoom='Enlarge screenshot', zoom_hint='Click to enlarge', viewer='Screenshot',
               zoom_in='Zoom in', zoom_out='Zoom out', fit='Fit', actual='100%', close='Close',
               lead='Evidence report. A prepared disk or a reachable SSH port alone is not proof of a completed installation.'),
    'it': dict(verdict='Esito', latest='Ultima schermata', commands='Comandi ed esiti',
               utc='UTC', command='Comando / dettaglio', secs='Secondi', outcome='Esito',
               timeline='Cronologia', logs='Code dei log', none='Nessuna schermata disponibile.',
               zoom='Ingrandisci schermata', zoom_hint='Clic per ingrandire', viewer='Schermata',
               zoom_in='Aumenta zoom', zoom_out='Riduci zoom', fit='Adatta', actual='100%', close='Chiudi',
               lead='Report di evidenze. Un disco preparato o una porta SSH raggiungibile non dimostrano da soli un\'installazione completata.'),
}


# Terminal decoration is not HTML content. Strip CSI (colors/cursor controls),
# OSC (titles/hyperlinks), and other escape strings, including their C1 forms.
TERMINAL_ESCAPE = re.compile(
    r'(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)'
    r'|(?:\x1b[P^_X]|[\x90\x98\x9e\x9f])[^\x1b\x9c]*(?:\x1b\\|\x9c|$)'
    r'|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]'
    r'|\x1b[ -/]*[@-Z\\-_]'
)


def terminal_text(value):
    """Readable log text, without changing the original evidence on disk."""
    value = TERMINAL_ESCAPE.sub('', str(value)).replace('\r\n', '\n').replace('\r', '\n')
    chars = []
    for char in value:
        if char == '\b':
            if chars and chars[-1] != '\n':
                chars.pop()
        elif char in '\n\t' or (ord(char) >= 32 and not 127 <= ord(char) <= 159):
            chars.append(char)
    return ''.join(chars)


def report(lab, pdf=False, dry=False, open_after=False):
    output = lab.safe('out', lab.vm + '.html')
    if dry:
        print(f'Generate self-contained HTML: {output}' + (' and optional PDF' if pdf else '')
              + (f'; then open it with xdg-open' if open_after else ''))
        return output
    # One language per report, chosen by LAB_LANG, instead of "English / Italiano"
    # welded into every heading. The quick guide below stays Italian by design.
    head = HEADINGS.get(lab.cfg.get('LAB_LANG', 'en'), HEADINGS['en'])
    history = records(lab.work / 'events.jsonl')
    frames = records(lab.work / 'screenshots' / 'timeline.jsonl')
    installations = [e for e in history if e.get('kind') == 'installation']
    verdict = installations[-1].get('outcome', 'unknown') if installations else 'Not yet validated / Non ancora validata'
    assisted = any(e.get('kind') == 'intervention' for e in history)
    if assisted:
        verdict += ' — history includes explicit keyboard intervention'
    # Exposed and used are different claims: one is a fact about the setup, the other
    # about what could have reached the guest. Only the second weakens the verdict.
    if any(e.get('kind') == 'console-client' for e in history):
        verdict += ' — a client connected to the graphical console, so the run is not provably unattended'
    elif any(e.get('kind') == 'console' for e in history):
        verdict += ' — a graphical console was exposed; no client connected to it'
    if len(installations) > 1:
        verdict += f' — attempt {len(installations)}; previous outcomes retained below'
    def moment(value):
        return datetime.fromtimestamp(value, timezone.utc)

    def stamp(value):
        return moment(value).isoformat(timespec='seconds')

    def clock(value):
        # Only the wall clock in the cell: the full ISO instant lives in the tooltip,
        # so a fixed narrow column cannot shred the timestamp character by character.
        return moment(value).strftime('%H:%M:%S')

    def text(value):
        # Redact after stripping decoration so ANSI cannot split a secret, then
        # escape HTML: guest output must never become active report markup.
        return escape(lab.redact(terminal_text(value)))

    def render_frame(frame):
        path = lab.safe('work', lab.vm, 'screenshots', frame['file'])
        caption = text(stamp(frame['time']) + ' — ' + frame['caption'])
        hint = ''
        if not path.is_file():
            body = '<p>Evidence was explicitly removed.</p>'
        elif path.suffix == '.png':
            body = ('<button type="button" class="screen-zoom" aria-label="' + head['zoom'] + '">'
                    '<img alt="Guest framebuffer" src="data:image/png;base64,'
                    + base64.b64encode(path.read_bytes()).decode() + '"></button>')
            hint = '<span class="zoom-hint">' + head['zoom_hint'] + '</span>'
        else:
            body = '<pre>' + text(path.read_text(errors='replace')) + '</pre>'
        return f'<figure>{body}<figcaption><span class="frame-caption">{caption}</span>{hint}</figcaption></figure>'

    def row(event):
        command = event.get('command', event.get('detail', event.get('kind', '')))
        return ('<tr><td class="t" title="' + escape(stamp(event['time'])) + '">'
                + escape(clock(event['time'])) + '</td><td class="c"><code>'
                + text(command) + '</code></td><td class="s">'
                + text(event.get('duration', '—')) + '</td><td class="o">'
                + text(event.get('outcome', 'started')) + '</td></tr>')
    rows = [row(event) for event in history]
    latest = render_frame(frames[-1]) if frames else f'<p class="muted">{head["none"]}</p>'
    logs = ''.join('<h3>' + name + '</h3><pre>' + text(tail(lab.work / name)) + '</pre>'
                   for name in ('serial.log', 'steps.log', 'qemu.log'))
    doc = f'''<!doctype html><html lang="{lab.cfg.get('LAB_LANG', 'en')}"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QEMU playground — {escape(lab.vm)}</title><style>{STYLE}</style>
<header><p class="muted">QEMU PLAYGROUND · {escape(stamp(__import__('time').time()))}</p><h1>{escape(lab.vm)}</h1>
<p class="verdict">{head['verdict']}: {text(verdict)}</p><p class="muted">{escape(head['lead'])}</p></header>
<h2>{head['latest']}</h2>{latest}
<article><h2>{head['commands']}</h2><table>
<colgroup><col style="width:5.2em"><col><col style="width:4.6em"><col style="width:11em"></colgroup>
<tr><th>{head['utc']}</th><th>{head['command']}</th><th>{head['secs']}</th><th>{head['outcome']}</th></tr>{''.join(rows)}</table></article>
<h2>{head['timeline']} <span class="muted">({len(frames)})</span></h2>
<div class="grid">{''.join(render_frame(f) for f in frames)}</div>
<article><h2>{head['logs']}</h2>{logs}</article>
<article lang="it"><h2>Guida rapida</h2><p>Il menu mostra il comando prima di eseguirlo. Le operazioni lunghe proseguono in background: Ctrl-C chiude la visualizzazione e torna al menu.</p>
<pre>./lab status
./lab ssh {lab.vm} -- {'"ver"' if lab.vm == 'windows-11' else '"uname -a"'}
./lab shot {lab.vm}
./lab stop {lab.vm}
./lab clean {lab.vm} disk seed --dry-run</pre>
<p>Gli screenshot sono passivi. Solo <code>--nudge</code> invia un tasto e rende il giro assistito. In caso di timeout il disco e la VM rimangono disponibili per la diagnosi. Per una nuova installazione, ferma la VM e scegli esplicitamente cosa cancellare. ISO e chiavi condivise sono escluse da <code>clean all</code>.</p></article>
<dialog id="screen-viewer" aria-labelledby="viewer-title" aria-describedby="viewer-caption">
<div class="viewer-toolbar"><span id="viewer-title">{head['viewer']}</span>
<button type="button" id="viewer-minus" aria-label="{head['zoom_out']}">−</button>
<output id="viewer-scale" aria-live="polite">100%</output>
<button type="button" id="viewer-plus" aria-label="{head['zoom_in']}">+</button>
<button type="button" id="viewer-fit">{head['fit']}</button>
<button type="button" id="viewer-actual">{head['actual']}</button>
<button type="button" id="viewer-close" autofocus>{head['close']} · Esc</button></div>
<div class="viewer-viewport"><img id="viewer-image" alt=""></div><p id="viewer-caption"></p>
</dialog>
{VIEWER_SCRIPT}
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
    if open_after:
        open_file(output.with_suffix('.pdf') if pdf else output)
    return output
