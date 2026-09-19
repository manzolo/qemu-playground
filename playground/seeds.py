"""Render guest installation inputs. No host shell interpolation."""
import base64
import json
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path
from .core import LabError, atomic, run
from .desktop import (AUTOSTART, SDDM_CONF, SESSION_CONF, XSETUP, lubuntu_check,
                      noblank_desktop, sddm_conf, session_conf, xsetup_script)


def lubuntu_seed(cfg, public_key, password_hash, token):
    def command(text):
        return ['sh', '-c', text]

    def install_file(path, content, mode):
        # base64 so a config file's own quoting cannot collide with the three
        # levels of shell between here and the target filesystem.
        blob = base64.b64encode(content.encode()).decode()
        return command('curtin in-target -- sh -c ' + shlex.quote(
            f'mkdir -p "$(dirname {path})" && echo {blob} | base64 -d > {path} '
            f'&& chmod {mode} {path}'))
    data = {'autoinstall': {
        'version': 1, 'interactive-sections': [], 'refresh-installer': {'update': False},
        'locale': cfg['LAB_LOCALE'], 'keyboard': {'layout': cfg['LAB_KEYBOARD']},
        'timezone': cfg['LAB_TIMEZONE'],
        'identity': {'hostname': cfg['LAB_HOSTNAME'], 'username': cfg['LAB_USER'], 'password': password_hash},
        'ssh': {'install-server': True, 'allow-pw': False, 'authorized-keys': [public_key.strip()]},
        'storage': {'layout': {'name': 'direct'}},
        # The desktop is required: an unreachable mirror must fail, never leave a
        # server-only guest carrying a successful installation token.
        'apt': {'geoip': False, 'fallback': 'abort'},
        'packages': ['lubuntu-desktop', 'qemu-guest-agent'],
        'late-commands': [
            command('curtin in-target -- systemctl enable serial-getty@ttyS0.service'),
            command('curtin in-target -- systemctl enable sddm.service'),
            command('curtin in-target -- systemctl set-default graphical.target'),
            install_file(XSETUP, xsetup_script(cfg), '0755'),
            install_file(SDDM_CONF, sddm_conf(cfg), '0644'),
            install_file(SESSION_CONF, session_conf(cfg), '0644'),
            install_file(AUTOSTART, noblank_desktop(), '0644'),
            command('curtin in-target -- sh -c ' + shlex.quote(lubuntu_check(cfg))),
            command('sync && blockdev --flushbufs /dev/vda && printf "\\nLAB_OK_' + token + '\\n" > /dev/ttyS0')],
        'error-commands': [command('sync; printf "\\nLAB_FAIL_' + token + '\\n" > /dev/ttyS0')],
        'shutdown': 'poweroff'}}
    # JSON is valid YAML 1.2; cloud-init accepts it after its identifying header.
    return '#cloud-config\n' + json.dumps(data, indent=2, ensure_ascii=False) + '\n'


def windows_seed(cfg, public_key, token, template):
    ns = 'urn:schemas-microsoft-com:unattend'
    wcm = 'http://schemas.microsoft.com/WMIConfig/2002/State'
    ET.register_namespace('', ns)
    ET.register_namespace('wcm', wcm)
    root = ET.Element('{' + ns + '}unattend')
    def child(parent, tag, value=None, **attrs):
        node = ET.SubElement(parent, '{' + ns + '}' + tag, attrs)
        if value is not None:
            node.text = str(value)
        return node
    def component(settings, name):
        return child(settings, 'component', name=name, processorArchitecture='amd64',
                     publicKeyToken='31bf3856ad364e35', language='neutral', versionScope='nonSxS')
    lang = cfg['LAB_WINDOWS_LANGUAGE']
    def locales(node):
        for k in ('InputLocale', 'SystemLocale', 'UILanguage', 'UserLocale'):
            child(node, k, '0410:00000410' if k == 'InputLocale' and cfg['LAB_KEYBOARD'] == 'it' else
                  ('0409:00000409' if k == 'InputLocale' else lang))
        return node
    pe = child(root, 'settings', **{'pass': 'windowsPE'})
    intl = component(pe, 'Microsoft-Windows-International-Core-WinPE')
    child(child(intl, 'SetupUILanguage'), 'UILanguage', lang)
    locales(intl)
    setup = component(pe, 'Microsoft-Windows-Setup')
    dc = child(setup, 'DiskConfiguration')
    disk = child(dc, 'Disk', **{'{' + wcm + '}action': 'add'})
    child(disk, 'DiskID', 0)
    child(disk, 'WillWipeDisk', 'true')
    create = child(disk, 'CreatePartitions')
    for order, typ, size in [(1, 'EFI', 260), (2, 'MSR', 16), (3, 'Primary', None)]:
        part = child(create, 'CreatePartition', **{'{' + wcm + '}action': 'add'})
        child(part, 'Order', order)
        child(part, 'Type', typ)
        child(part, 'Size' if size else 'Extend', size if size else 'true')
    modify = child(disk, 'ModifyPartitions')
    for order, fmt, label in [(1, 'FAT32', 'System'), (3, 'NTFS', 'Windows')]:
        part = child(modify, 'ModifyPartition', **{'{' + wcm + '}action': 'add'})
        child(part, 'Order', 1 if order == 1 else 2)
        child(part, 'PartitionID', order)
        child(part, 'Format', fmt)
        child(part, 'Label', label)
        if order == 3:
            child(part, 'Letter', 'C')
    child(dc, 'WillShowUI', 'OnError')
    image = child(child(setup, 'ImageInstall'), 'OSImage')
    meta = child(child(image, 'InstallFrom'), 'MetaData', **{'{' + wcm + '}action': 'add'})
    child(meta, 'Key', '/IMAGE/NAME')
    child(meta, 'Value', cfg['LAB_WINDOWS_IMAGE'])
    target = child(image, 'InstallTo')
    child(target, 'DiskID', 0)
    child(target, 'PartitionID', 3)
    child(image, 'WillShowUI', 'OnError')
    user = child(setup, 'UserData')
    child(user, 'AcceptEula', 'true')
    # An explicitly empty <Key/> is what suppresses the product key page on retail
    # multi-edition media; the edition still comes from ImageInstall /IMAGE/NAME and
    # Windows is left unactivated. A ProductKey carrying only WillShowUI does NOT
    # skip it: Setup stopped forever on the modal "Product Key" page (2026-09-16).
    key = child(user, 'ProductKey')
    child(key, 'Key', '')
    child(key, 'WillShowUI', 'Never')
    # Nothing runs in specialize on purpose: a RunSynchronousCommand that exits
    # non-zero there stops Setup on a modal "unexpected restart" dialog forever
    # (observed 2026-09-16 with a Get-Volume copy step). The bootstrap script is
    # started from the seed CD at first logon instead.
    special = child(root, 'settings', **{'pass': 'specialize'})
    shell = component(special, 'Microsoft-Windows-Shell-Setup')
    child(shell, 'ComputerName', cfg['LAB_HOSTNAME'])
    child(shell, 'TimeZone', cfg['LAB_WINDOWS_TIMEZONE'])
    locales(component(special, 'Microsoft-Windows-International-Core'))
    oobe = child(root, 'settings', **{'pass': 'oobeSystem'})
    # International-Core again here, or OOBE stops on the region/keyboard pages.
    locales(component(oobe, 'Microsoft-Windows-International-Core'))
    shell = component(oobe, 'Microsoft-Windows-Shell-Setup')
    settings = child(shell, 'OOBE')
    for key in ('HideEULAPage', 'HideLocalAccountScreen', 'HideOnlineAccountScreens',
                'HideWirelessSetupInOOBE'):
        child(settings, key, 'true')
    child(settings, 'ProtectYourPC', 3)
    accounts = child(child(shell, 'UserAccounts'), 'LocalAccounts')
    account = child(accounts, 'LocalAccount', **{'{' + wcm + '}action': 'add'})
    child(account, 'Name', cfg['LAB_USER'])
    child(account, 'Group', 'Administrators')
    for parent in (account,):
        password = child(parent, 'Password')
        child(password, 'Value', cfg['LAB_PASSWORD'])
        child(password, 'PlainText', 'true')
    auto = child(shell, 'AutoLogon')
    child(auto, 'Username', cfg['LAB_USER'])
    child(auto, 'Enabled', 'true')
    # 999, not 1: an intermediate OOBE reboot must not consume the only autologon
    # before FirstLogonCommands runs. setup.ps1 clears AutoAdminLogon when it ends.
    child(auto, 'LogonCount', 999)
    password = child(auto, 'Password')
    child(password, 'Value', cfg['LAB_PASSWORD'])
    child(password, 'PlainText', 'true')
    first = child(child(shell, 'FirstLogonCommands'), 'SynchronousCommand', **{'{' + wcm + '}action': 'add'})
    child(first, 'Order', 1)
    # Setup stores FirstLogonCommands as HKLM RunOnce values and silently ignores
    # any longer than MAX_PATH (260), so this stays one short line that finds the
    # seed CD by its own drive letter instead of copying anything beforehand.
    logon = ('cmd.exe /c for %d in (D E F G) do if exist %d:\\setup.ps1 '
             'powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\\setup.ps1')
    assert len(logon) < 260, len(logon)
    child(first, 'CommandLine', logon)
    script = (template.replace('@PUBLIC_KEY_B64@', base64.b64encode(public_key.strip().encode()).decode())
              .replace('@TOKEN@', token)
              .replace('@QGA_SHA256@', cfg['LAB_QGA_SHA256'].strip().lower()))
    return ET.tostring(root, encoding='unicode', xml_declaration=True), script


def render(lab, token):
    cfg = lab.cfg
    if not cfg['LAB_PASSWORD']:
        raise LabError('Run ./lab config init first, or set LAB_PASSWORD in .env')
    public = lab.safe('keys', 'id_ed25519.pub').read_text().strip()
    folder = lab.safe('work', lab.vm, 'seed')
    folder.mkdir(exist_ok=True, mode=0o700)
    if lab.vm == 'lubuntu-26.04':
        hashed = run(['openssl', 'passwd', '-6', '-stdin'], input=cfg['LAB_PASSWORD'] + '\n', capture=True).strip()
        atomic(folder / 'user-data', lubuntu_seed(cfg, public, hashed, token))
        atomic(folder / 'meta-data', json.dumps({'instance-id': token, 'local-hostname': cfg['LAB_HOSTNAME']}))
    else:
        xml, script = windows_seed(cfg, public, token, (lab.root / 'templates' / 'windows-setup.ps1').read_text())
        atomic(folder / 'autounattend.xml', xml)
        atomic(folder / 'setup.ps1', script)
    return folder
