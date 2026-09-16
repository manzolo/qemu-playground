"""PPM to PNG using the standard library; passive, deduplicated evidence."""
import hashlib
import json
import struct
import time
import zlib
from .core import LabError, append_json, read_json, records, run, tail
from .protocol import qmp


def ppm(data):
    # P6 header whitespace/comments; consume exactly one raster separator so a
    # first pixel with value 10/32 is never accidentally eaten as whitespace.
    pos, tokens = 0, []
    while len(tokens) < 4:
        while pos < len(data) and data[pos] in b' \r\n\t':
            pos += 1
        if pos < len(data) and data[pos] == 35:
            pos = data.index(b'\n', pos) + 1
            continue
        end = pos
        while end < len(data) and data[end] not in b' \r\n\t':
            end += 1
        tokens.append(data[pos:end])
        pos = end
    if tokens[0] != b'P6' or tokens[3] != b'255':
        raise LabError('Unsupported screendump: expected 8-bit P6 PPM')
    width, height = map(int, tokens[1:3])
    pos += 2 if data[pos:pos+2] == b'\r\n' else 1
    pixels = data[pos:]
    if width <= 0 or height <= 0 or len(pixels) != width * height * 3:
        raise LabError('Truncated or invalid PPM framebuffer')
    return width, height, pixels


def png(width, height, pixels):
    def chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
    rows = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def shot(lab, *, open_image=False, nudge=False, caption='Manual screenshot', dedupe=False):
    lab.ensure()
    folder = lab.safe('work', lab.vm, 'screenshots')
    folder.mkdir(exist_ok=True, mode=0o700)
    if nudge:
        lab.event(kind='intervention', command='QMP send-key ret', outcome='assisted', detail='Explicit --nudge')
        print('WARNING: explicit --nudge sends Enter. This run is now assisted.', flush=True)
        qmp(lab, 'send-key', {'keys': [{'type': 'qcode', 'data': 'ret'}]})
    stamp = str(time.time_ns())
    raw = folder / (stamp + '.ppm')
    qmp(lab, 'screendump', {'filename': str(raw)})
    try:
        width, height, pixels = ppm(raw.read_bytes())
    finally:
        raw.unlink(missing_ok=True)
    if max(pixels, default=0) <= 8:
        content = lab.redact(tail(lab.serial) or '[Black framebuffer; serial log is empty]')
        payload, suffix = content.encode(), '.txt'
        caption += ' — black framebuffer; serial log tail'
    else:
        payload, suffix = png(width, height, pixels), '.png'
    digest = hashlib.sha256(payload).hexdigest()
    timeline = folder / 'timeline.jsonl'
    previous = records(timeline)
    attempt_start = read_json(lab.work / 'attempt.json', {}).get('start', 0)
    assisted = any(e.get('kind') == 'intervention' and e['time'] >= attempt_start for e in records(lab.work / 'events.jsonl'))
    if assisted:
        caption += ' — ASSISTED: a key was sent explicitly'
    if dedupe and previous and previous[-1]['digest'] == digest:
        return folder / previous[-1]['file']
    target = folder / (stamp + suffix)
    target.write_bytes(payload)
    append_json(timeline, dict(time=time.time(), file=target.name, digest=digest, caption=caption))
    print(f'Screenshot: {target}', flush=True)
    if open_image:
        run(['xdg-open', target], timeout=20)
    return target
