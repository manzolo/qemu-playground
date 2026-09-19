"""Configuration, filesystem boundaries, bounded processes and observable state."""
from __future__ import annotations
import contextlib
from contextvars import ContextVar
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import time

ACTIVE_LAB = ContextVar('active_lab', default=None)
PROFILES = ('lubuntu-26.04', 'windows-11')
DEFAULTS = dict(LAB_USER='labuser', LAB_PASSWORD='', LAB_LOCALE='it_IT.UTF-8',
    LAB_KEYBOARD='it', LAB_TIMEZONE='Europe/Rome', LAB_HOSTNAME='playground',
    LAB_DISK_GB='64', LAB_RAM_MB='4096', LAB_CPUS='2', LAB_SSH_PORT='2400',
    LAB_ACCEL='kvm', LAB_INSTALL_TIMEOUT='7200', LAB_SHOT_INTERVAL='20',
    LAB_WINDOWS_IMAGE='Windows 11 Pro', LAB_WINDOWS_LANGUAGE='it-IT',
    LAB_WINDOWS_TIMEZONE='W. Europe Standard Time',
    LAB_OVMF_CODE='/usr/share/OVMF/OVMF_CODE_4M.ms.fd',
    LAB_OVMF_VARS='/usr/share/OVMF/OVMF_VARS_4M.ms.fd',
    LAB_QGA_MSI='', LAB_QGA_SHA256='', LAB_QGA_SOURCE='', LAB_LANG='en',
    LAB_VNC_PORT='5940', LAB_AUDIO='none')

class LabError(Exception):
    pass


PACKAGES = {'qemu-system-x86_64': 'qemu-system-x86', 'qemu-img': 'qemu-utils',
            'fzf': 'fzf', 'xorriso': 'xorriso', '7z': '7zip', 'genisoimage': 'genisoimage',
            'python3': 'python3', 'curl': 'curl', 'ssh': 'openssh-client',
            'ssh-keygen': 'openssh-client', 'openssl': 'openssl', 'swtpm': 'swtpm',
            'tmux': 'tmux', 'xdg-open': 'xdg-utils'}
ESSENTIAL = ('qemu-system-x86_64', 'qemu-img', 'xorriso', 'genisoimage', 'curl',
             'ssh', 'ssh-keygen', 'openssl')


def readiness(lab):
    """Host facts, and nothing printed: `doctor` renders them, the menu states them.

    A menu entry cannot call `doctor` to learn whether it is done, because `doctor`
    reports as it checks. Both read the same facts from here instead.
    """
    tools = {cmd: shutil.which(cmd) for cmd in PACKAGES}
    firmware = {key: Path(lab.cfg[key]).is_file() for key in ('LAB_OVMF_CODE', 'LAB_OVMF_VARS')}
    kvm = os.access('/dev/kvm', os.R_OK | os.W_OK)
    available = next((int(line.split()[1]) // 1024
                      for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:')), 0)
    free = shutil.disk_usage(lab.root).free // 2**30
    essential = ESSENTIAL + (('7z', 'swtpm') if lab.vm == 'windows-11' else ())
    ready = (all(tools[cmd] for cmd in essential)
             and (kvm or lab.cfg['LAB_ACCEL'] == 'tcg')
             and available >= int(lab.cfg['LAB_RAM_MB']) and free >= 10)
    missing = {PACKAGES[cmd] for cmd, path in tools.items() if not path}
    if not all(firmware.values()):
        missing.add('ovmf')
    return dict(tools=tools, firmware=firmware, kvm=kvm, available=available,
                free=free, ready=ready, missing=missing)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as f:
            os.chmod(tmp, 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def append_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(value, ensure_ascii=False) + '\n')
        f.flush()


def records(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(errors='replace').splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass  # A crashed writer must not make evidence unreadable.
    return result


def tail(path, size=16000):
    try:
        with Path(path).open('rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - size))
            return f.read().decode(errors='replace')
    except OSError:
        return ''


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def lock(path, timeout=5):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LabError(f'Timeout waiting for lock {path}; another operation is active')
                time.sleep(.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def busy(path):
    if not path.exists():
        return False
    with path.open('r') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def run(argv, *, timeout=120, dry=False, capture=False, input=None):
    argv = [str(v) for v in argv]
    command = shlex.join(argv)
    print('$ ' + command, flush=True)
    if dry:
        return ''
    lab = ACTIVE_LAB.get()
    started = time.monotonic()
    if lab:
        lab.event(kind='process-start', command=command, outcome='running')
    outcome = 'failed'
    try:
        p = subprocess.run(argv, timeout=timeout, check=False, text=True,
                           input=input, capture_output=capture)
        outcome = 'passed' if not p.returncode else f'exit {p.returncode}'
        if p.returncode:
            raise LabError(f'{argv[0]} exited {p.returncode}: {(p.stderr or "").strip()}')
        return p.stdout if capture else ''
    except FileNotFoundError as e:
        raise LabError(f'Missing command: {argv[0]}; run ./lab doctor') from e
    except subprocess.TimeoutExpired as e:
        outcome = f'timeout after {timeout}s waiting for {argv[0]}'
        raise LabError(f'Timeout after {timeout}s waiting for {argv[0]}: {command}') from e
    finally:
        if lab:
            lab.event(kind='process-end', command=command, outcome=outcome,
                      duration=round(time.monotonic() - started, 2))


class Lab:
    def __init__(self, root, vm='lubuntu-26.04'):
        if vm not in PROFILES:
            raise LabError(f'Unknown profile: {vm}')
        self.root = Path(root).absolute()
        if any(c in str(self.root) for c in ',\n\r'):
            raise LabError('Lab path must not contain commas or newlines (QEMU option syntax)')
        self.vm = vm
        self.cfg = DEFAULTS.copy()
        self.sources = dict.fromkeys(DEFAULTS, 'default')
        env = self.safe('.env')
        if env.is_file():
            for n, line in enumerate(env.read_text().splitlines(), 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                key, sep, value = line.partition('=')
                key, value = key.strip(), value.strip()
                # Accepted only for old local configs; Lubuntu always has LXQt.
                if sep and key == 'LAB_DESKTOP':
                    continue
                if not sep or key not in DEFAULTS:
                    raise LabError(f'.env:{n}: unknown or invalid setting {key}')
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                self.cfg[key], self.sources[key] = value, '.env'
        self.validate()
        self.profile = read_json(self.root / 'profiles' / (vm + '.json'))
        if not self.profile:
            raise LabError(f'Missing profile: {vm}')
        self.work = self.safe('work', vm)
        self.disk = self.safe('work', vm, 'disk.qcow2')
        self.seed = self.safe('work', vm, 'seed.iso')
        self.iso = self.safe('iso', self.profile['filename'])
        self.qmp = self.work / 'qmp.sock'
        self.qga = self.work / 'qga.sock'
        self.pidfile = self.work / 'qemu.pid'
        self.serial = self.work / 'serial.log'
        self.log = self.work / 'steps.log'
        self.oplock = self.work / 'operation.lock'
        self.port = int(self.cfg['LAB_SSH_PORT']) + (vm == 'windows-11')
        # Off unless asked for: a graphical console is also a keyboard into the guest.
        base = int(self.cfg['LAB_VNC_PORT'])
        self.vnc = base + (vm == 'windows-11') if base else 0
        self.name = 'playground-' + hashlib.sha256(str(self.work).encode()).hexdigest()[:16]

    def safe(self, *parts):
        path = self.root.joinpath(*parts)
        # No symlinks in managed paths, even if their current target is inside the lab.
        for component in [path, *path.parents]:
            if component == self.root.parent:
                break
            if component.is_symlink():
                raise LabError(f'Refusing symlink in managed path: {component}')
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise LabError(f'Path escapes lab: {path}')
        return path

    def validate(self):
        for key, low, high in [('LAB_DISK_GB', 16, 4096), ('LAB_RAM_MB', 1024, 1048576),
                ('LAB_CPUS', 1, 256), ('LAB_SSH_PORT', 2400, 65534),
                ('LAB_INSTALL_TIMEOUT', 10, 86400), ('LAB_SHOT_INTERVAL', 1, 3600)]:
            try:
                value = int(self.cfg[key])
            except ValueError:
                raise LabError(f'{key} must be an integer')
            if not low <= value <= high:
                raise LabError(f'{key} must be between {low} and {high}')
        for key, pattern in [('LAB_USER', r'[a-z][a-z0-9_-]{0,19}'),
                ('LAB_HOSTNAME', r'[a-zA-Z][a-zA-Z0-9-]{0,14}'),
                ('LAB_LOCALE', r'[A-Za-z0-9_.@-]+'), ('LAB_KEYBOARD', r'[a-z0-9,-]+'),
                ('LAB_TIMEZONE', r'[A-Za-z0-9_+/-]+')]:
            if not re.fullmatch(pattern, self.cfg[key]):
                raise LabError(f'Invalid {key}')
        if self.cfg['LAB_ACCEL'] not in ('kvm', 'tcg'):
            raise LabError('LAB_ACCEL must be kvm or tcg')
        if self.cfg['LAB_AUDIO'] not in ('none', 'pipewire', 'pa', 'alsa', 'jack', 'oss', 'dbus', 'sdl'):
            raise LabError('LAB_AUDIO must be none or a QEMU audio backend (see qemu-system-x86_64 -audiodev help)')
        if self.cfg['LAB_LANG'] not in ('en', 'it'):
            raise LabError('LAB_LANG must be en or it')
        try:
            vnc = int(self.cfg['LAB_VNC_PORT'])
        except ValueError:
            raise LabError('LAB_VNC_PORT must be an integer')
        if vnc and not 5900 <= vnc <= 5998:
            raise LabError('LAB_VNC_PORT must be 0 (off) or between 5900 and 5998')
        if any(any(ord(c) < 32 for c in value) for value in self.cfg.values()):
            raise LabError('Configuration values must not contain control characters')

    def ensure(self):
        self.safe('work', self.vm).mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.work, 0o700)

    def redact(self, text):
        for secret in [self.cfg['LAB_PASSWORD']]:
            if secret:
                text = text.replace(secret, '[REDACTED]')
        return text

    def event(self, **value):
        self.ensure()
        value.setdefault('time', time.time())
        value.setdefault('profile', self.vm)
        append_json(self.safe('work', self.vm, 'events.jsonl'),
                    json.loads(self.redact(json.dumps(value))))

    def pid(self):
        try:
            pid = int(self.pidfile.read_text().strip())
            args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
            exe = Path(args[0]).name
            # Validate binary, unique name, QMP endpoint AND disk, never a TCP port.
            valid = (exe.startswith('qemu-system-') and
                     '-name' in args and args[args.index('-name') + 1] == self.name and
                     f'unix:{self.qmp},server=on,wait=off' in args and
                     f'file={self.disk},if=none,id=os,format=qcow2' in args)
            return pid if valid else None
        except (OSError, ValueError, IndexError):
            return None

    def iso_state(self, full=False):
        if not self.iso.is_file():
            return 'missing ISO'
        st = self.iso.stat()
        fingerprint = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
        cache = self.work / 'iso-check.json'
        previous = read_json(cache, {})
        expected = self.profile['sha256'].lower()
        if not full and previous.get('fingerprint') == fingerprint and previous.get('expected') == expected:
            digest = previous.get('digest')
        elif not full:
            return 'ISO not verified (run iso verify)'
        else:
            digest = sha256(self.iso)
            self.ensure()
            atomic(cache, json.dumps(dict(fingerprint=fingerprint, digest=digest, expected=expected)))
        return 'verified' if digest == expected else 'SHA-256 MISMATCH: delete or redownload ISO'

    def ssh_banner(self, timeout=1.0):
        # The banner normally comes back in about 6 ms, but this answer drives what the
        # menu says, and a probe that gives up too early makes the state lie: entries
        # showed [todo] and then refused as [blocked] when a parallel install starved
        # the guest of CPU. One second is ~150x the usual latency and still bounded.
        if not self.pid():
            return False
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=timeout) as s:
                s.settimeout(timeout)
                return s.recv(255).startswith(b'SSH-')
        except OSError:
            return False

    def blockers(self):
        reasons = []
        state = self.iso_state()
        if state != 'verified':
            reasons.append(state)
        if self.vm == 'windows-11':
            for key in ('LAB_QGA_MSI', 'LAB_OVMF_CODE', 'LAB_OVMF_VARS'):
                if not self.cfg[key] or not Path(self.cfg[key]).is_file():
                    reasons.append(f'missing {key}')
            if not re.fullmatch('[a-fA-F0-9]{64}', self.cfg['LAB_QGA_SHA256']) or not self.cfg['LAB_QGA_SOURCE']:
                reasons.append('missing QGA vendor checksum/provenance')
            if int(self.cfg['LAB_RAM_MB']) < 4096 or int(self.cfg['LAB_DISK_GB']) < 64 or int(self.cfg['LAB_CPUS']) < 2:
                reasons.append('Windows needs 4096 MiB RAM, 64 GiB disk and 2 CPUs')
        return reasons


# Typeable on purpose. This password is never accepted by SSH on either guest: it
# exists so a human can log in at the graphical console, where a 36-character random
# string is unusable. Upper case, lower case and digits keep it acceptable to Windows
# even where the local complexity policy is switched on.
PASSWORD_WORDS = ('Kernel', 'Serial', 'Socket', 'Initrd', 'Casper', 'Console',
                  'Pflash', 'Virtio', 'Chardev', 'Machine')


def lab_password():
    return f'{secrets.choice(PASSWORD_WORDS)}-{secrets.randbelow(9000) + 1000}'


def port_free(port):
    """Can we still bind this localhost port? Three labs can forward the same one."""
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def open_file(path):
    """Hand a produced artefact to the desktop, detached and silenced.

    A viewer's own chatter is not lab diagnostics: inheriting the terminal buries
    the step log under its warnings, and waiting on it would block the menu.
    """
    argv = ['xdg-open', str(path)]
    print('$ ' + shlex.join(argv), flush=True)
    if not shutil.which('xdg-open'):
        print('xdg-open is missing; open it yourself: ' + str(path), flush=True)
        return
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def confirm(message, yes=False):
    if yes:
        return
    if not os.isatty(0) or input(message + ' [y/N] ').strip().lower() not in ('y', 'yes'):
        raise LabError('Cancelled; use --yes only after reviewing the printed paths/command')
