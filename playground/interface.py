"""Filesystem-derived menu model shared by Bash and the CLI."""
import shlex
from .core import PROFILES, Lab, busy, port_free, read_json, readiness, records


def status(lab):
    for vm in PROFILES:
        item = Lab(lab.root, vm)
        disk = item.disk.stat().st_size if item.disk.is_file() else 0
        history = [e for e in records(item.work / 'events.jsonl') if e.get('kind') == 'installation']
        used = sum(p.stat().st_blocks * 512 for p in item.work.rglob('*') if p.is_file() and not p.is_symlink()) if item.work.exists() else 0
        lines = [f'{vm}:',
                 f'  ISO: {item.iso_state()}',
                 f'  Disk: {disk} bytes; allocated workspace: {used // 2**20} MiB',
                 f'  Owned PID: {item.pid() or "stopped"}; operation: {"active" if busy(item.oplock) else "idle"}',
                 f'  SSH 127.0.0.1:{item.port}: {"responding" if item.ssh_banner() else "not responding"}']
        if item.vnc:
            lines.append(f'  Graphical console: vnc://127.0.0.1:{item.vnc}')
        lines.append(f'  Last installation: {history[-1]["outcome"] if history else "not validated"}')
        print('\n'.join(lines))
        if item.blockers():
            print('  Preparation blocked: ' + '; '.join(item.blockers()))


# User-facing menu strings. Code, logs and reports stay English; LAB_LANG only
# changes this chrome. Unknown blocker reasons fall back to their English text.
TEXT = {
    'en': dict(
        unavailable='Unavailable',
        groups=('Setup', 'Media', 'Machine', 'Access', 'Reports', 'Cleanup', 'Menu'),
        busy='an operation is running', running='the VM is already running',
        vm_off='the VM is off', no_disk='no disk yet', no_iso='the ISO is missing',
        no_prep='not prepared yet', used='disk already used; clean it explicitly',
        no_serial='no serial log yet', no_ssh='SSH is not answering', no_vnc='LAB_VNC_PORT is 0; set it and start again',
        taken='port {} is held by something this lab does not own',
        win_console='Windows: use screenshot, ssh or agent instead',
        win_import='import the Microsoft ISO: see docs/WINDOWS.md',
        win_again='import a newer Microsoft ISO',
        keys='Enter run · Ctrl-P profile · Ctrl-R/F5 refresh · Esc quit\nCtrl-Y command to copy · Ctrl-L logs · Tab preview',
        h_on='VM running', h_off='VM stopped', h_busy='operation running',
        h_iso_ok='ISO verified', h_iso_no='ISO missing', h_vnc='console',
        hint='Ctrl-Y opens the command as plain text for copying.',
        copy_hint='Select the command and copy with your terminal shortcut. Any key returns.',
        doctor='Check and install prerequisites', cfg_show='Show active configuration',
        cfg_init='Create local configuration', iso_get='Download / resume the ISO',
        iso_check='Verify the ISO SHA-256', iso_again='Re-download the ISO',
        iso_del='Delete the ISO', prepare='Prepare disk, key and seed',
        install='Install unattended', start='Start the VM', stop='Stop the VM',
        recover='Recover a lost verdict from the logs',
        no_attempt='no installation attempt to recover',
        settled='this attempt already has a verdict',
        status='Lab status', console='Serial console (read-only)',
        shot='Screenshot and open', follow='Follow screenshots (every 2 seconds)', view='Open the graphical console',
        shell='Open an SSH session',
        ssh='Run a demo SSH command',
        agent='Guest agent: ping', html='Generate and open the HTML report',
        pdf='Generate and open the PDF report', preview='Preview cleaning disk and seed',
        clean='Delete {}', t_disk='the disk', t_seed='the seed', t_shots='the screenshots',
        t_logs='the logs', t_keys='the shared SSH key', t_out='the reports',
        t_all='everything except ISO and keys', quit='Exit the menu'),
    'it': dict(
        unavailable='Non disponibile',
        groups=('Ambiente', 'Supporti', 'Macchina', 'Accesso', 'Report', 'Pulizia', 'Menu'),
        busy='operazione in corso', running='la VM e\' gia\' accesa',
        vm_off='la VM e\' spenta', no_disk='manca il disco', no_iso='manca la ISO',
        no_prep='manca la preparazione', used='disco gia\' usato; pulirlo esplicitamente',
        no_serial='manca il log seriale', no_ssh='SSH non risponde', no_vnc='LAB_VNC_PORT e\' 0; impostalo e riavvia',
        taken='la porta {} e\' occupata da un processo non nostro',
        win_console='Windows: usa screenshot, ssh o agent',
        win_import='importa la ISO Microsoft: vedi docs/WINDOWS.md',
        win_again='importa una ISO Microsoft piu\' recente',
        keys='Invio esegui · Ctrl-P profilo · Ctrl-R/F5 aggiorna · Esc esci\nCtrl-Y comando da copiare · Ctrl-L log · Tab anteprima',
        h_on='VM accesa', h_off='VM spenta', h_busy='operazione in corso',
        h_iso_ok='ISO verificata', h_iso_no='ISO mancante', h_vnc='console',
        hint='Ctrl-Y apre il comando come testo semplice da copiare.',
        copy_hint='Seleziona il comando e copia con la scorciatoia del terminale. Un tasto per tornare.',
        doctor='Verifica e installa i prerequisiti', cfg_show='Mostra la configurazione attiva',
        cfg_init='Crea la configurazione locale', iso_get='Scarica / riprendi la ISO',
        iso_check='Verifica lo SHA-256 della ISO', iso_again='Riscarica la ISO',
        iso_del='Elimina la ISO', prepare='Prepara disco, chiave e seed',
        install='Installa senza assistenza', start='Avvia la VM', stop='Ferma la VM',
        recover='Recupera dai log un esito perduto',
        no_attempt='nessun tentativo di installazione da recuperare',
        settled='questo tentativo ha già un esito',
        status='Stato del laboratorio', console='Console seriale (sola lettura)',
        shot='Screenshot e apertura', follow='Segui screenshot (ogni 2 secondi)', view='Apri la console grafica',
        shell='Apri una sessione SSH',
        ssh='Esegui un comando SSH dimostrativo',
        agent='Guest agent: ping', html='Genera e apri il report HTML',
        pdf='Genera e apri il report PDF', preview='Anteprima pulizia di disco e seed',
        clean='Elimina {}', t_disk='il disco', t_seed='il seed', t_shots='gli screenshot',
        t_logs='i log', t_keys='la chiave SSH condivisa', t_out='i report',
        t_all='tutto salvo ISO e chiavi', quit='Esci dal menu'),
}

# Reasons produced by Lab.blockers(), which stays English like the rest of the API.
REASONS_IT = {
    'missing ISO': 'manca la ISO',
    'ISO not verified (run iso verify)': 'ISO non verificata (esegui iso verify)',
    'SHA-256 MISMATCH: delete or redownload ISO': 'SHA-256 NON CORRISPONDE: elimina o riscarica la ISO',
    'missing QGA vendor checksum/provenance': 'manca checksum/provenienza dell\'MSI del guest agent',
    'Windows needs 4096 MiB RAM, 64 GiB disk and 2 CPUs': 'Windows richiede 4096 MiB di RAM, 64 GiB di disco e 2 CPU',
}


def words(lab):
    return TEXT.get(lab.cfg.get('LAB_LANG', 'en'), TEXT['en'])


def translate(lab, reason):
    if lab.cfg.get('LAB_LANG') != 'it':
        return reason
    return '; '.join(REASONS_IT.get(part, part) for part in reason.split('; '))


def header(lab):
    """Compact VM context and shortcuts, without long paths or console URLs."""
    t = words(lab)
    iso = lab.iso_state()
    state = [lab.vm, t['h_on'] if lab.pid() else t['h_off'],
             t['h_iso_ok'] if iso == 'verified' else
             (t['h_iso_no'] if iso == 'missing ISO' else translate(lab, iso))]
    if busy(lab.oplock):
        state.append(t['h_busy'])
    return ' · '.join(state) + '\n' + t['keys']


def menu_command(lab, item):
    """One shell-safe line, identical to the action dispatched by the menu."""
    if item['command'] == ['_quit']:
        return ''
    suffix = ['--background'] if item['background'] else []
    return shlex.join([str(lab.root / 'lab')] + item['command'] + suffix)


def menu_row(item):
    """Keep unavailable actions visible, with a quiet category column."""
    label_color = '90' if not item['enabled'] else '39'
    return f'\033[90m{item["group"]:<10}\033[{label_color}m{item["label"]}\033[0m'


def menu_items(lab):
    t = words(lab)
    running = bool(lab.pid())
    active = busy(lab.oplock)
    disk = lab.disk.is_file() and lab.disk.stat().st_size > 0
    seed = lab.seed.is_file()
    reasons = translate(lab, '; '.join(lab.blockers()))
    # Probed once per redraw: asking twice doubled the cost and let two entries
    # disagree about the same fact within a single screen.
    reachable = running and lab.ssh_banner()
    v, items = lab.vm, []

    def add(label, cmd, blocked='', done=False, background=False):
        status = 'blocked' if blocked else ('done' if done else 'todo')
        category = {
            'doctor': 0, 'config': 0, 'iso': 1,
            'prepare': 2, 'install': 2, 'start': 2, 'stop': 2, 'status': 2, 'recover': 2,
            'console': 3, 'shot': 3, 'view': 3, 'ssh': 3, 'agent': 3,
            'report': 4, 'clean': 5, '_quit': 6,
        }[cmd[0]]
        group = t['groups'][category]
        items.append(dict(id=str(len(items)), label=label, status=status, reason=blocked,
                          state=t['unavailable'] + ': ' + blocked if blocked else '',
                          group=group, row=f'{group:<10}{label}', command=cmd,
                          enabled=not blocked, background=background))

    mutation = t['busy'] if active else (t['running'] if running else '')
    windows = v == 'windows-11'
    add(t['doctor'], ['doctor', '--install'], done=readiness(lab)['ready'])
    add(t['cfg_show'], ['config', 'show'], done=(lab.root / '.env').exists())
    add(t['cfg_init'], ['config', 'init'], done=(lab.root / '.env').exists())
    add(t['iso_get'], ['iso', v, 'download'], mutation or (t['win_import'] if windows else ''),
        done=lab.iso_state() == 'verified', background=True)
    add(t['iso_check'], ['iso', v, 'verify'], mutation or ('' if lab.iso.exists() else t['no_iso']),
        done=lab.iso_state() == 'verified', background=True)
    add(t['iso_again'], ['iso', v, 'redownload'], mutation or (t['win_again'] if windows else ''), background=True)
    add(t['iso_del'], ['iso', v, 'delete'], mutation)
    add(t['prepare'], ['prepare', v], mutation or reasons, done=disk and seed, background=True)
    add(t['install'], ['install', v], mutation or reasons or ('' if disk and seed else t['no_prep'])
        or (t['used'] if (lab.work / 'attempt.json').exists() else ''), background=True)
    # A port held by a process we do not own would otherwise show as a plain [todo]
    # that fails the moment it is chosen.
    stolen = '' if running or port_free(lab.port) else t['taken'].format(lab.port)
    add(t['start'], ['start', v], (t['busy'] if active else '') or (t['running'] if running else '')
        or stolen or ('' if disk else t['no_disk']))
    add(t['stop'], ['stop', v], '' if running else t['vm_off'], background=True)
    add(t['status'], ['status'], done=True)
    # Only offered where it can do something: an attempt whose watcher never
    # wrote a verdict, on a guest that has stopped.
    attempt = read_json(lab.work / 'attempt.json', {})
    settled = [e for e in records(lab.work / 'events.jsonl') if e.get('kind') == 'installation'
               and e.get('time', 0) >= attempt.get('start', 0)]
    add(t['recover'], ['recover', v], (t['busy'] if active else '') or
        ('' if attempt else t['no_attempt']) or (t['settled'] if settled else '')
        or (t['running'] if running else ''), done=bool(settled))
    add(t['console'], ['console', v], t['win_console'] if windows
        else ('' if lab.serial.exists() else t['no_serial']))
    add(t['shot'], ['shot', v], '' if running else t['vm_off'])
    add(t['follow'], ['shot', v, '--follow'], '' if running else t['vm_off'])
    add(t['view'], ['view', v], (t['no_vnc'] if not lab.vnc else '') or ('' if running else t['vm_off']))
    add(t['shell'], ['ssh', v], '' if reachable else t['no_ssh'])
    add(t['ssh'], ['ssh', v, '--', 'ver' if windows else 'uname -a'],
        '' if reachable else t['no_ssh'])
    add(t['agent'], ['agent', v, 'ping'], '' if running else t['vm_off'])
    add(t['html'], ['report', v, '--open'])
    add(t['pdf'], ['report', v, '--pdf', '--open'])
    add(t['preview'], ['clean', v, 'disk', 'seed', '--dry-run'], mutation)
    for target in ('disk', 'seed', 'screenshots', 'logs', 'keys', 'out', 'all'):
        short = {'screenshots': 't_shots', 'logs': 't_logs', 'keys': 't_keys',
                 'out': 't_out', 'all': 't_all'}.get(target, 't_' + target)
        add(t['clean'].format(t[short]), ['clean', v, target], mutation)
    # Last, and always available: leaving should not depend on knowing a key.
    add(t['quit'], ['_quit'])
    return items
