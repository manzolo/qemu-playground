"""Small, state-driven screens over the existing menu action catalogue."""
import shlex

from .core import busy, read_json, records
from .interface import header, menu_items, words
from .operations import configuration_digest


TEXT = {
    'en': dict(
        home='Guided setup', profiles='Choose a profile', tools='Settings and maintenance',
        setup='Prerequisites and configuration', media='Installation media',
        machine='Machine operations', access='Use the VM', reports='Reports', cleanup='Cleanup',
        back='Back', next='Next', refresh='Refresh progress',
        steps=('Environment', 'ISO', 'Preparation', 'Installation', 'Use the VM'),
        step='Step {}/5 · {}',
        host='Check the host before creating your virtual machine.',
        config='Create the local configuration and login password.',
        iso='Get the installation image. Its checksum must be verified to continue.',
        prepare='Create the virtual disk and unattended installation files.',
        install='Everything is prepared. Start the unattended installation.',
        start='Installation completed. Start the installed virtual machine.',
        running='The VM is running. Open a console or connect over SSH.',
        waiting='An operation is running. Refresh to see the next step; Ctrl-L opens the live log.',
        recovery='This disk has an unvalidated installation attempt. Inspect the report and logs before retrying.',
        unknown='This disk has no preparation record. Inspect it under Machine operations before continuing.',
        blocked='Resolve the preparation requirements in configuration, then refresh.',
        win_import='Import the Windows ISO',
        import_help='Follow docs/WINDOWS.md, then import your Microsoft ISO:',
        subkeys='Enter select · Esc back · Ctrl-P profile · Ctrl-R/F5 refresh',
        homekeys='Enter continue · Esc quit · Ctrl-P profile · Ctrl-R/F5 refresh',
        shortcuts='Ctrl-Y copy command · Ctrl-L logs · Tab details',
        return_hint='Press any key to return to the wizard.',
    ),
    'it': dict(
        home='Configurazione guidata', profiles='Scegli un profilo', tools='Impostazioni e manutenzione',
        setup='Prerequisiti e configurazione', media='Supporti di installazione',
        machine='Operazioni sulla macchina', access='Usa la VM', reports='Report', cleanup='Pulizia',
        back='Indietro', next='Prossimo', refresh='Aggiorna avanzamento',
        steps=('Ambiente', 'ISO', 'Preparazione', 'Installazione', 'Usa la VM'),
        step='Passo {}/5 · {}',
        host='Verifica il computer prima di creare la macchina virtuale.',
        config='Crea la configurazione locale e la password di accesso.',
        iso='Procurati la ISO di installazione e verificane il checksum per proseguire.',
        prepare='Crea il disco virtuale e i file per installare senza assistenza.',
        install='La preparazione è completa. Avvia l’installazione senza assistenza.',
        start='Installazione completata. Avvia la macchina virtuale installata.',
        running='La VM è accesa. Apri una console oppure collegati via SSH.',
        waiting='Operazione in corso. Aggiorna per vedere il prossimo passo; Ctrl-L apre il log in diretta.',
        recovery='Il disco ha un tentativo di installazione non validato. Controlla report e log prima di riprovare.',
        unknown='Il disco non ha un record di preparazione. Controllalo in Operazioni sulla macchina.',
        blocked='Risolvi i requisiti di preparazione nella configurazione, poi aggiorna.',
        win_import='Importa la ISO di Windows',
        import_help='Segui docs/WINDOWS.md, poi importa la tua ISO Microsoft:',
        subkeys='Invio seleziona · Esc indietro · Ctrl-P profilo · Ctrl-R/F5 aggiorna',
        homekeys='Invio continua · Esc esci · Ctrl-P profilo · Ctrl-R/F5 aggiorna',
        shortcuts='Ctrl-Y copia comando · Ctrl-L log · Tab dettagli',
        return_hint='Premi un tasto per tornare al percorso guidato.',
    ),
}

PAGES = ('home', 'profiles', 'tools', 'setup', 'media', 'machine', 'access', 'reports', 'cleanup')
PARENTS = {page: ('tools' if page in ('setup', 'media', 'machine', 'cleanup') else 'home')
           for page in PAGES}


def words_for(lab):
    return TEXT.get(lab.cfg.get('LAB_LANG'), TEXT['en'])


def navigation(lab, target, label=None):
    t = words_for(lab)
    return dict(id='nav:' + target, label=label or t[target], group='', enabled=True,
                command=[], background=False, state='', reason='')


def next_step(lab, items):
    """Recommend one action; never infer installation success from a disk or SSH."""
    t = words_for(lab)

    def action(*command):
        return next(i for i in items if i['command'] == list(command))

    v = lab.vm
    attempt = read_json(lab.work / 'attempt.json', {})
    disk = lab.disk.is_file() and lab.disk.stat().st_size > 0
    prepared = read_json(lab.work / 'prepared.json', {})
    if busy(lab.oplock):
        step = 4 if attempt else (3 if disk else 2)
        return step, t['waiting'], navigation(lab, 'home', t['refresh'])
    if disk and (lab.work / 'attempt.json').exists():
        history = [e for e in records(lab.work / 'events.jsonl')
                   if e.get('kind') == 'installation' and e.get('time', 0) >= attempt.get('start', float('inf'))]
        if not history or not history[-1].get('outcome', '').startswith('passed'):
            return 4, t['recovery'], action('report', v, '--open')
        if not lab.pid():
            return 5, t['start'], action('start', v)
    if lab.pid():
        return 5, t['running'], navigation(lab, 'access')
    if action('doctor', '--install')['status'] != 'done':
        return 1, t['host'], action('doctor', '--install')
    if not lab.cfg['LAB_PASSWORD']:
        return 1, t['config'], action('config', 'init')
    iso = lab.iso_state()
    if iso != 'verified':
        if v == 'windows-11' and not lab.iso.is_file():
            return 2, t['iso'], navigation(lab, 'media', t['win_import'])
        operation = 'verify' if lab.iso.is_file() else 'download'
        if 'MISMATCH' in iso:
            operation = 'delete' if v == 'windows-11' else 'redownload'
        return 2, t['iso'], action('iso', v, operation)
    if lab.blockers():
        return 3, t['blocked'] + '\n' + action('prepare', v)['reason'], navigation(lab, 'setup')
    if disk and not prepared:
        return 3, t['unknown'], navigation(lab, 'machine')
    if (action('prepare', v)['status'] != 'done'
            or prepared.get('config') != configuration_digest(lab)):
        return 3, t['prepare'], action('prepare', v)
    return 4, t['install'], action('install', v)


def screen(lab, page='home'):
    t = words_for(lab)
    items = menu_items(lab)
    intro = t[page]
    if page == 'home':
        step, explanation, primary = next_step(lab, items)
        intro += '\n' + t['step'].format(step, t['steps'][step - 1]) + '\n' + explanation
        if primary.get('state'):
            intro += '\n' + primary['state']
        rows = [dict(primary, group=t['next'])]
        rows += [navigation(lab, target) for target in ('access', 'reports', 'tools', 'profiles')
                 if primary['id'] != 'nav:' + target]
        rows.append(items[-1])
    elif page == 'profiles':
        rows = [dict(navigation(lab, 'home', label), id='profile:' + profile)
                for profile, label in (('lubuntu-26.04', 'Lubuntu 26.04'), ('windows-11', 'Windows 11'))]
    elif page == 'tools':
        rows = [navigation(lab, target) for target in ('setup', 'media', 'machine', 'cleanup')]
    else:
        groups = dict(setup=0, media=1, machine=2, access=3, reports=4, cleanup=5)
        rows = [i for i in items if i['group'] == words(lab)['groups'][groups[page]]]
        if page == 'access':
            rows += [i for i in items if i['command'][0] in ('start', 'stop')]
        if page == 'media' and lab.vm == 'windows-11':
            intro += '\n' + t['import_help'] + '\n' + shlex.join(
                [str(lab.root / 'lab'), 'iso', lab.vm, 'import', '--source', '/path/to/vendor.iso'])
    if page != 'home':
        rows.append(navigation(lab, PARENTS[page], t['back']))
    context = header(lab).splitlines()[0]
    keys = t['homekeys'] if page == 'home' else t['subkeys']
    return context + '\n\n' + intro + '\n\n' + keys + '\n' + t['shortcuts'], rows
