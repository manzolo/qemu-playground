"""Filesystem-derived menu model shared by Bash and the CLI."""
import json
import shutil
from .core import PROFILES, Lab, busy, records


def status(lab):
    for vm in PROFILES:
        item = Lab(lab.root, vm)
        disk = item.disk.stat().st_size if item.disk.is_file() else 0
        history = [e for e in records(item.work / 'events.jsonl') if e.get('kind') == 'installation']
        used = sum(p.stat().st_blocks * 512 for p in item.work.rglob('*') if p.is_file() and not p.is_symlink()) if item.work.exists() else 0
        print(f'{vm}:\n  ISO: {item.iso_state()}\n  Disk: {disk} bytes; allocated workspace: {used // 2**20} MiB'
              f'\n  Owned PID: {item.pid() or "stopped"}; operation: {"active" if busy(item.oplock) else "idle"}'
              f'\n  SSH 127.0.0.1:{item.port}: {"responding" if item.ssh_banner() else "not responding"}'
              f'\n  Last installation: {history[-1]["outcome"] if history else "not validated"}')
        if item.blockers():
            print('  Preparation blocked: ' + '; '.join(item.blockers()))


def menu_items(lab):
    running = bool(lab.pid())
    active = busy(lab.oplock)
    disk = lab.disk.is_file() and lab.disk.stat().st_size > 0
    seed = lab.seed.is_file()
    blockers = lab.blockers()
    reasons = '; '.join(blockers)
    v = lab.vm
    items = []
    def add(label, cmd, blocked='', done=False, background=False):
        state = '[bloccato: ' + blocked + ']' if blocked else ('[fatto]' if done else '[da fare]')
        items.append(dict(id=str(len(items)), label=label, state=state, command=cmd,
                          enabled=not blocked, background=background))
    mutation = 'operazione in corso' if active else ('VM accesa' if running else '')
    add('Verifica e installa prerequisiti', ['doctor', '--install'])
    add('Mostra configurazione attiva', ['config', 'show'], done=(lab.root / '.env').exists())
    add('Crea configurazione locale', ['config', 'init'], done=(lab.root / '.env').exists())
    add('Scarica / riprendi ISO', ['iso', v, 'download'], mutation or ('importa la ISO Microsoft: vedi docs/WINDOWS.md' if v == 'windows-11' else ''), done=lab.iso_state() == 'verified', background=True)
    add('Verifica SHA-256 ISO', ['iso', v, 'verify'], mutation or ('' if lab.iso.exists() else 'manca la ISO'), done=lab.iso_state() == 'verified', background=True)
    add('Riscarica ISO', ['iso', v, 'redownload'], mutation or ('importa una nuova ISO Microsoft' if v == 'windows-11' else ''), background=True)
    add('Elimina ISO', ['iso', v, 'delete'], mutation)
    add('Prepara disco, chiave e seed', ['prepare', v], mutation or reasons, done=disk and seed, background=True)
    add('Installa senza assistenza', ['install', v], mutation or reasons or ('' if disk and seed else 'manca preparazione') or
        ('disco già usato: pulizia esplicita necessaria' if (lab.work / 'attempt.json').exists() else ''), background=True)
    add('Avvia VM', ['start', v], ('operazione in corso' if active else '') or ('già accesa' if running else '') or ('' if disk else 'manca il disco'))
    add('Ferma VM', ['stop', v], '' if running else 'VM spenta', background=True)
    add('Stato del laboratorio', ['status'], done=True)
    add('Console seriale (sola lettura)', ['console', v], 'Windows: usa screenshot, SSH o agent' if v == 'windows-11' else ('' if lab.serial.exists() else 'manca il log seriale'))
    add('Screenshot e apertura', ['shot', v], '' if running else 'VM spenta')
    add('Esegui comando SSH dimostrativo', ['ssh', v, '--', 'ver' if v == 'windows-11' else 'uname -a'], '' if running and lab.ssh_banner() else 'SSH non disponibile')
    add('Guest agent: ping', ['agent', v, 'ping'], '' if running else 'VM spenta')
    add('Genera report HTML', ['report', v])
    add('Genera report PDF', ['report', v, '--pdf'])
    add('Anteprima pulizia disco e seed', ['clean', v, 'disk', 'seed', '--dry-run'], mutation)
    for target, label in [('disk', 'disco'), ('seed', 'seed'), ('screenshots', 'screenshot'), ('logs', 'log'), ('keys', 'chiave SSH condivisa'), ('out', 'report'), ('all', 'tutto salvo ISO e chiavi')]:
        add('Elimina ' + label, ['clean', v, target], mutation)
    return items
