"""Lifecycle operations. Failed disks are retained until explicit selective cleanup."""
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import time
from .core import (Lab, LabError, PROFILES, atomic, busy, confirm, lock, read_json,
                   records, run, sha256, tail)
from .protocol import agent, qmp
from .screens import shot
from .seeds import render


def doctor(lab, install=False, dry=False):
    packages = {'qemu-system-x86_64': 'qemu-system-x86', 'qemu-img': 'qemu-utils',
                'fzf': 'fzf', 'xorriso': 'xorriso', '7z': '7zip', 'genisoimage': 'genisoimage',
                'python3': 'python3', 'curl': 'curl', 'ssh': 'openssh-client',
                'ssh-keygen': 'openssh-client', 'openssl': 'openssl', 'swtpm': 'swtpm',
                'tmux': 'tmux', 'xdg-open': 'xdg-utils'}
    missing = set()
    for cmd, package in packages.items():
        path = shutil.which(cmd)
        print(f'{"OK" if path else "MISSING":8} {cmd:22} {path or "apt install " + package}')
        if not path:
            missing.add(package)
    kvm = os.access('/dev/kvm', os.R_OK | os.W_OK)
    print(f'{"OK" if kvm else "BLOCKED":8} /dev/kvm: ' +
          ('accessible' if kvm else 'enable virtualization/KVM and ask the host administrator for kvm group access; log in again'))
    for key in ('LAB_OVMF_CODE', 'LAB_OVMF_VARS'):
        exists = Path(lab.cfg[key]).is_file()
        print(f'{"OK" if exists else "MISSING":8} {key}: {lab.cfg[key]}')
        if not exists:
            missing.add('ovmf')
    available = next((int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:')), 0)
    free = shutil.disk_usage(lab.root).free // 2**30
    print(f'RAM available: {available} MiB; requested per VM: {lab.cfg["LAB_RAM_MB"]} MiB')
    print(f'Disk free: {free} GiB; qcow2 maximum: {lab.cfg["LAB_DISK_GB"]} GiB (+ ISO/Windows staging)')
    print('Optional PDF: python3-markdown python3-weasyprint (loaded only by report --pdf).')
    print('Optional libguestfs: unreadable /boot/vmlinuz-* prevents appliance creation as a normal user.')
    if missing:
        cmd = ['sudo', 'apt-get', 'install', '--'] + sorted(missing)
        print('Suggested: ' + shlex.join(cmd))
        if install:
            if not dry:
                confirm('Install these prerequisite packages?')
            run(cmd, timeout=1800, dry=dry)
    essential = ['qemu-system-x86_64', 'qemu-img', 'xorriso', 'genisoimage', 'curl', 'ssh', 'ssh-keygen', 'openssl']
    if lab.vm == 'windows-11':
        essential += ['7z', 'swtpm']
    ready = all(shutil.which(cmd) for cmd in essential)
    ready = ready and (kvm or lab.cfg['LAB_ACCEL'] == 'tcg')
    ready = ready and available >= int(lab.cfg['LAB_RAM_MB']) and free >= 10
    return 0 if ready else 1


def config(lab, action, dry=False):
    if action == 'show':
        for key, value in lab.cfg.items():
            if key == 'LAB_PASSWORD':
                value = '[set, hidden]' if value else '[not set]'
            print(f'{key}={value}  ({lab.sources[key]})')
        return
    path = lab.safe('.env')
    if path.exists():
        print(f'Already exists: {path}; edit this file to change configuration.')
        return
    print(f'Create {path} (0600), with a random local password; never used by SSH.')
    if not dry:
        password = secrets.token_urlsafe(24) + 'aA1!'
        source = (lab.root / '.env.example').read_text().replace('LAB_PASSWORD=\n', f'LAB_PASSWORD={password}\n')
        atomic(path, source)


def iso(lab, action, *, source=None, yes=False, dry=False):
    if lab.pid():
        raise LabError('Stop the owned VM before changing or verifying installation media')
    if action == 'verify':
        print(f'SHA-256: {lab.iso}\nExpected: {lab.profile["sha256"]}\nSource: {lab.profile["source"]}')
        if not dry:
            state = lab.iso_state(full=True)
            if state != 'verified':
                raise LabError(state + f'; use ./lab iso {lab.vm} delete')
            print('Verified against pinned vendor checksum.')
        return
    if action == 'redownload' and not lab.profile.get('url'):
        raise LabError('No stable Windows download URL. Explicitly delete then import matching Microsoft media; nothing removed.')
    if action in ('delete', 'redownload'):
        paths = [lab.iso, lab.iso.with_suffix('.iso.part')]
        for path in paths:
            print(f'Delete: {path} ({path.stat().st_size if path.exists() else 0} bytes)')
        if not dry:
            confirm('Delete these ISO files?', yes)
            for path in paths:
                lab.safe('iso', path.name).unlink(missing_ok=True)
        if action == 'delete':
            return
    if action == 'import':
        if source is None or not Path(source).is_file():
            raise LabError('Use --source /path/to/vendor.iso')
        print(f'Import {source} -> {lab.iso}; expected SHA-256 {lab.profile["sha256"]}')
        if dry:
            return
        if lab.iso.exists():
            raise LabError('ISO already exists; verify it or explicitly delete it first')
        if sha256(source) != lab.profile['sha256']:
            raise LabError('Source SHA-256 MISMATCH; nothing imported')
        lab.iso.parent.mkdir(exist_ok=True, mode=0o700)
        part = lab.safe('iso', lab.iso.name + '.part')
        shutil.copyfile(source, part)
        part.replace(lab.iso)
    else:
        url = lab.profile.get('url')
        if not url and not lab.iso.exists():
            message = 'Windows ISO must be downloaded from Microsoft and imported with iso import --source; see docs/WINDOWS.md'
            if dry:
                print(message)
                return
            raise LabError(message)
        if lab.iso.exists() and action != 'redownload':
            print('ISO already exists; verifying instead of overwriting.')
        else:
            if not dry:
                lab.iso.parent.mkdir(exist_ok=True, mode=0o700)
            part = lab.safe('iso', lab.iso.name + '.part')
            run(['curl', '--fail', '--location', '--show-error', '--connect-timeout', '20',
                 '--max-time', '7200', '--retry', '3', '-C', '-', '-o', part, url], timeout=7500, dry=dry)
            if not dry:
                part.replace(lab.iso)
    if not dry:
        state = lab.iso_state(full=True)
        if state != 'verified':
            raise LabError(state + f'; use ./lab iso {lab.vm} delete')


def ensure_key(lab):
    folder = lab.safe('keys')
    folder.mkdir(exist_ok=True, mode=0o700)
    with lock(folder / 'key.lock'):
        key, public = lab.safe('keys', 'id_ed25519'), lab.safe('keys', 'id_ed25519.pub')
        if not key.exists():
            if public.exists():
                raise LabError('Public key exists without private key; clean keys explicitly before regenerating')
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'qemu-playground', '-f', key])
        os.chmod(key, 0o600)
        if not public.exists():
            atomic(public, run(['ssh-keygen', '-y', '-f', key], capture=True))


def prepare(lab, dry=False):
    if lab.pid():
        raise LabError('Stop the VM before preparing it')
    token = secrets.token_hex(12)
    if dry:
        run(['qemu-img', 'create', '-f', 'qcow2', lab.disk, lab.cfg['LAB_DISK_GB'] + 'G'], dry=True)
        print(f'Render {lab.vm} seed using dedicated keys/id_ed25519.pub; no secrets printed.')
        if lab.vm == 'ubuntu-26.04':
            for name in ('vmlinuz', 'initrd'):
                run(['xorriso', '-osirrox', 'on', '-indev', lab.iso, '-extract', '/casper/' + name, lab.work / name], dry=True)
        else:
            print('Verify signed QGA MSI; stage Windows ISO and use efisys_noprompt.bin (no injected keys).')
        run(['genisoimage', '-quiet', '-J', '-r', '-V', 'cidata' if lab.vm == 'ubuntu-26.04' else 'QPL_SEED',
             '-o', lab.seed, lab.work / 'seed'], dry=True)
        return
    if lab.blockers():
        raise LabError('; '.join(lab.blockers()))
    if lab.disk.exists() and not (lab.work / 'prepared.json').exists():
        raise LabError('Existing disk has no preparation provenance; preserved. Clean disk explicitly before preparation.')
    if lab.iso_state(full=True) != 'verified':
        raise LabError('Installation ISO failed verification')
    if (lab.work / 'attempt.json').exists():
        raise LabError('An installation already used this disk. Evidence is retained; use clean disk/seed explicitly for a new attempt.')
    if not lab.cfg['LAB_PASSWORD']:
        raise LabError('Run ./lab config init before prepare')
    lab.ensure()
    ensure_key(lab)
    folder = render(lab, token)
    if lab.vm == 'ubuntu-26.04':
        for name in ('vmlinuz', 'initrd'):
            target = lab.safe('work', lab.vm, name)
            if not target.exists():
                run(['xorriso', '-osirrox', 'on', '-indev', lab.iso, '-extract', '/casper/' + name, target], timeout=300)
    else:
        msi = Path(lab.cfg['LAB_QGA_MSI'])
        if sha256(msi) != lab.cfg['LAB_QGA_SHA256'].lower():
            raise LabError('QGA MSI checksum mismatch; obtain the expected checksum from its trusted distributor')
        shutil.copyfile(msi, folder / 'qemu-ga-x64.msi')
        firmware = lab.safe('work', lab.vm, 'OVMF_VARS.fd')
        if not firmware.exists():
            shutil.copyfile(lab.cfg['LAB_OVMF_VARS'], firmware)
        tree = lab.safe('work', lab.vm, 'windows-media')
        boot = lab.safe('work', lab.vm, 'installer.iso')
        if not boot.exists():
            tree.mkdir(exist_ok=True, mode=0o700)
            run(['7z', 'x', '-y', '-o' + str(tree), lab.iso], timeout=900)
            candidates = list(tree.rglob('*'))
            efi = next((p for p in candidates if p.name.lower() == 'efisys_noprompt.bin'), None)
            if efi is None:
                raise LabError('Windows media lacks efisys_noprompt.bin; refusing an installer that requires hidden key presses')
            run(['xorriso', '-as', 'mkisofs', '-iso-level', '3', '-J', '-joliet-long', '-V', 'QPL_WINDOWS',
                 '-e', str(efi.relative_to(tree)), '-no-emul-boot', '-o', boot, tree], timeout=900)
    if not lab.disk.exists():
        run(['qemu-img', 'create', '-f', 'qcow2', lab.disk, lab.cfg['LAB_DISK_GB'] + 'G'])
    else:
        run(['qemu-img', 'check', lab.disk], timeout=120)
    run(['genisoimage', '-quiet', '-J', '-r', '-V', 'cidata' if lab.vm == 'ubuntu-26.04' else 'QPL_SEED',
         '-o', lab.seed, folder], timeout=120)
    atomic(lab.work / 'prepared.json', json.dumps({'token': token, 'time': time.time(),
           'config': configuration_digest(lab), 'seed_sha256': sha256(lab.seed)}))


def configuration_digest(lab):
    import hashlib
    return hashlib.sha256(json.dumps(lab.cfg, sort_keys=True).encode()).hexdigest()


def qemu_command(lab, installing=False):
    windows = lab.vm == 'windows-11'
    cfg = lab.cfg
    cmd = ['qemu-system-x86_64', '-name', lab.name, '-machine',
           'q35,smm=on' if windows else 'q35', '-accel', cfg['LAB_ACCEL'], '-cpu',
           'host' if cfg['LAB_ACCEL'] == 'kvm' else 'max', '-m', cfg['LAB_RAM_MB'], '-smp', cfg['LAB_CPUS'],
           '-display', 'none', '-vga', 'std', '-daemonize', '-pidfile', str(lab.pidfile),
           '-qmp', f'unix:{lab.qmp},server=on,wait=off', '-monitor', 'none',
           '-serial', 'file:' + str(lab.serial),
           '-drive', f'file={lab.disk},if=none,id=os,format=qcow2',
           '-device', 'ide-hd,drive=os,bus=ide.0,bootindex=1' if windows else 'virtio-blk-pci,drive=os,bootindex=1',
           '-netdev', f'user,id=net,hostfwd=tcp:127.0.0.1:{lab.port}-:22',
           '-device', 'e1000e,netdev=net' if windows else 'virtio-net-pci,netdev=net',
           '-chardev', f'socket,path={lab.qga},server=on,wait=off,id=agent']
    if windows:
        cmd += ['-device', 'isa-serial,chardev=agent,index=1',
                '-global', 'driver=cfi.pflash01,property=secure,value=on',
                '-drive', f'if=pflash,format=raw,unit=0,readonly=on,file={cfg["LAB_OVMF_CODE"]}',
                '-drive', f'if=pflash,format=raw,unit=1,file={lab.work / "OVMF_VARS.fd"}',
                '-chardev', f'socket,id=tpm,path={lab.work / "tpm.sock"}',
                '-tpmdev', 'emulator,id=tpm0,chardev=tpm', '-device', 'tpm-tis,tpmdev=tpm0']
    else:
        cmd += ['-device', 'virtio-serial-pci', '-device', 'virtserialport,chardev=agent,name=org.qemu.guest_agent.0']
    if installing:
        media = lab.work / 'installer.iso' if windows else lab.iso
        cmd += ['-drive', f'file={media},media=cdrom,readonly=on,if=none,id=install',
                '-device', 'ide-cd,drive=install,bus=ide.1,bootindex=2',
                '-drive', f'file={lab.seed},media=cdrom,readonly=on,if=none,id=seed',
                '-device', 'ide-cd,drive=seed,bus=ide.2']
        if not windows:
            cmd += ['-kernel', str(lab.work / 'vmlinuz'), '-initrd', str(lab.work / 'initrd'),
                    '-append', 'autoinstall ds=nocloud console=tty0 console=ttyS0,115200n8']
    return cmd


def tpm_stop(lab):
    file = lab.work / 'tpm.pid'
    try:
        pid = int(file.read_text())
        args = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
        if Path(args[0]).name == 'swtpm' and f'dir={lab.work / "tpm"}' in args:
            fd = os.pidfd_open(pid)
            try:
                current = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
                if current != args:
                    raise LabError('TPM process identity changed; refusing to signal')
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
            deadline = time.monotonic() + 5
            while Path(f'/proc/{pid}/cmdline').exists() and time.monotonic() < deadline:
                if not Path(f'/proc/{pid}/cmdline').read_bytes():
                    break
                time.sleep(.1)
    except (OSError, ValueError):
        pass


def start(lab, *, installing=False, dry=False):
    cmd = qemu_command(lab, installing)
    if dry:
        if lab.vm == 'windows-11':
            run(tpm_command(lab), dry=True)
        run(cmd, dry=True)
        return
    if lab.pid():
        raise LabError('This VM is already running')
    if not lab.disk.is_file() or lab.disk.stat().st_size == 0:
        raise LabError('Missing/nonempty disk; run prepare first')
    if lab.cfg['LAB_ACCEL'] == 'kvm' and not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise LabError('KVM is unavailable; run doctor. LAB_ACCEL=tcg is an explicit slow emulation option.')
    if len(str(lab.qmp).encode()) > 103:
        raise LabError('Lab path is too long for Unix sockets; move it to a shorter path')
    for binary in ['qemu-system-x86_64'] + (['swtpm'] if lab.vm == 'windows-11' else []):
        if not shutil.which(binary):
            raise LabError(f'Missing {binary}; run doctor')
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', lab.port))
        except OSError as e:
            raise LabError(f'SSH port {lab.port} is already in use; no process will be stopped') from e
    lab.ensure()
    for path in (lab.pidfile, lab.qmp, lab.qga):
        lab.safe('work', lab.vm, path.name).unlink(missing_ok=True)
    # Keep serial output from each previous boot, since QEMU truncates file: logs.
    if lab.serial.exists():
        archive = lab.work / 'logs'
        archive.mkdir(exist_ok=True)
        lab.serial.rename(archive / f'serial-{time.time_ns()}.log')
    if lab.vm == 'windows-11':
        tpm_stop(lab)
        (lab.work / 'tpm').mkdir(exist_ok=True, mode=0o700)
        run(tpm_command(lab), timeout=15)
    atomic(lab.work / 'qemu-command.txt', shlex.join(cmd) + '\n')
    lab.event(kind='qemu', command=shlex.join(cmd), outcome='launch')
    try:
        with (lab.work / 'qemu.log').open('a') as log:
            print('$ ' + shlex.join(cmd), flush=True)
            proc = subprocess.run(cmd, stdout=log, stderr=log, timeout=30)
            if proc.returncode:
                raise LabError('QEMU launch failed: ' + tail(lab.work / 'qemu.log'))
        deadline = time.monotonic() + 10
        while not lab.pid() and time.monotonic() < deadline:
            time.sleep(.1)
        if not lab.pid():
            raise LabError('Timeout waiting for owned QEMU process after launch; see qemu.log')
    except BaseException:
        if not lab.pid():
            tpm_stop(lab)
        raise


def tpm_command(lab):
    return ['swtpm', 'socket', '--tpm2', '--tpmstate', f'dir={lab.work / "tpm"}',
            '--ctrl', f'type=unixio,path={lab.work / "tpm.sock"}', '--pid', f'file={lab.work / "tpm.pid"}',
            '--daemon', '--terminate']


def install(lab, dry=False, nudge=False):
    if dry:
        start(lab, installing=True, dry=True)
        print(f'Wait at most {lab.cfg["LAB_INSTALL_TIMEOUT"]}s for completion token AND spontaneous QEMU exit; keep failed disk.')
        return
    if lab.pid():
        raise LabError('VM already running')
    prep = read_json(lab.work / 'prepared.json', {})
    if not prep or not lab.seed.is_file() or prep.get('seed_sha256') != sha256(lab.seed):
        raise LabError('Missing or changed seed; run prepare')
    if prep.get('config') != configuration_digest(lab):
        raise LabError('Configuration changed since prepare; prepare again before installing')
    if lab.iso_state(full=True) != 'verified':
        raise LabError('Installation ISO is missing or does not match pinned checksum')
    if (lab.work / 'attempt.json').exists():
        raise LabError('Disk already used for an installation attempt. Preserved by default; clean disk explicitly before retrying.')
    atomic(lab.work / 'attempt.json', json.dumps({'token': prep['token'], 'start': time.time()}))
    started = time.monotonic()
    deadline = started + int(lab.cfg['LAB_INSTALL_TIMEOUT'])
    outcome = 'failed'
    success = False
    try:
        start(lab, installing=True)
        next_shot = 0
        nudged = False
        while True:
            output = tail(lab.serial, 1024 * 1024).splitlines()
            success = success or 'LAB_OK_' + prep['token'] in output
            if 'LAB_FAIL_' + prep['token'] in output:
                raise LabError('Installer emitted its failure token; inspect the last screen and guest log')
            alive = lab.pid()
            if not alive:
                if not success:
                    raise LabError('QEMU exited without the completion token; inspect the last screen and serial log')
                outcome = 'passed (assisted)' if nudge or any(e.get('kind') == 'intervention' and e['time'] >= read_json(lab.work / 'attempt.json')['start'] for e in records(lab.work / 'events.jsonl')) else 'passed (unattended)'
                print('Completion token received and QEMU exited on its own.', flush=True)
                break
            if time.monotonic() >= deadline:
                raise LabError(f'Timeout after {lab.cfg["LAB_INSTALL_TIMEOUT"]}s waiting for ' +
                               ('spontaneous QEMU shutdown after token' if success else 'installer completion token') +
                               '; VM and failed disk retained')
            if time.monotonic() >= next_shot:
                try:
                    shot(lab, caption='Installation progress', dedupe=True, nudge=nudge and not nudged)
                    nudged = nudge
                except (LabError, OSError) as e:
                    print(f'Screenshot warning: {e}', flush=True)
                next_shot = time.monotonic() + int(lab.cfg['LAB_SHOT_INTERVAL'])
            time.sleep(1)
    except BaseException as e:
        lab.event(kind='diagnostic', outcome='failed', detail=str(e), free_disk=shutil.disk_usage(lab.root).free,
                  memory=tail('/proc/meminfo'), qemu=tail(lab.work / 'qemu-command.txt'))
        try:
            if lab.pid():
                shot(lab, caption='FAILURE / TIMEOUT — ' + str(e))
        except (LabError, OSError) as screen_error:
            lab.event(kind='diagnostic', outcome='screenshot unavailable', detail=str(screen_error))
        raise
    finally:
        lab.event(kind='installation', outcome=outcome, duration=round(time.monotonic()-started, 2))
        if not lab.pid():
            tpm_stop(lab)
        from .report import report
        report(lab)


def stop(lab, *, force=False, dry=False):
    if dry:
        print(f'QMP system_powerdown; wait {180 if lab.vm == "windows-11" else 60}s; then QGA shutdown; wait 30s')
        if force:
            print('Then SIGTERM / SIGKILL only to the process verified against this lab paths.')
        return
    pid = lab.pid()
    if not pid:
        print('No owned VM is running. No process was signalled.')
        return
    try:
        qmp(lab, 'system_powerdown')
        acpi_wait = 180 if lab.vm == 'windows-11' else 60
    except (LabError, OSError) as e:
        print(f'ACPI shutdown unavailable: {e}; trying guest agent.', flush=True)
        acpi_wait = 0
    deadline = time.monotonic() + acpi_wait
    while lab.pid() and time.monotonic() < deadline:
        time.sleep(.5)
    if lab.pid():
        try:
            agent(lab, 'shutdown')
        except (LabError, OSError) as e:
            print(f'Guest agent shutdown unavailable: {e}')
        deadline = time.monotonic() + 30
        while lab.pid() and time.monotonic() < deadline:
            time.sleep(.5)
    if lab.pid() and not force:
        raise LabError('Timeout waiting for guest shutdown. VM retained; inspect shot, or explicitly use stop --force.')
    for sig in (signal.SIGTERM, signal.SIGKILL):
        owned = lab.pid()
        if not owned:
            break
        if owned != pid:
            raise LabError('Process identity changed during shutdown; refusing to signal')
        # pidfd binds the signal to the actual process, eliminating PID-reuse races.
        fd = os.pidfd_open(pid)
        try:
            if lab.pid() != pid:
                raise LabError('Process identity changed before signal')
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)
        deadline = time.monotonic() + 10
        while lab.pid() and time.monotonic() < deadline:
            time.sleep(.2)
    if lab.pid():
        raise LabError('Timeout waiting for owned QEMU exit after force stop')
    tpm_stop(lab)


def ssh_command(lab, command):
    cmd = ['ssh', '-F', '/dev/null', '-i', str(lab.safe('keys', 'id_ed25519')),
           '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no',
           '-o', 'KbdInteractiveAuthentication=no', '-o', 'StrictHostKeyChecking=accept-new',
           '-o', 'UserKnownHostsFile=' + str(lab.safe('keys', 'known_hosts')),
           '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=10',
           '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
           '-p', str(lab.port), lab.cfg['LAB_USER'] + '@127.0.0.1']
    # One argument is an explicit remote command string; multiple arguments are argv.
    if lab.vm == 'windows-11':
        remote = command[0] if len(command) == 1 else subprocess.list2cmdline(command)
        cmd += ['cmd.exe /d /s /c "' + remote + '"']
    else:
        remote = command[0] if len(command) == 1 else shlex.join(command)
        cmd += ['sh -lc ' + shlex.quote(remote)]
    return cmd


def ssh(lab, command, *, dry=False, timeout=120):
    if not command:
        raise LabError('Use lab ssh <vm> -- <command>; password prompts are never enabled')
    cmd = ssh_command(lab, command)
    if dry:
        run(cmd, dry=True)
        return 0
    if not lab.pid():
        raise LabError('SSH refused: no owned VM is running')
    if not lab.safe('keys', 'id_ed25519').is_file():
        raise LabError('Missing dedicated lab SSH key')
    print('$ ' + shlex.join(cmd), flush=True)
    try:
        return subprocess.run(cmd, timeout=timeout).returncode
    except subprocess.TimeoutExpired as e:
        raise LabError(f'Timeout after {timeout}s waiting for guest command: {command}') from e


def clean(lab, targets, *, yes=False, dry=False):
    mapping = {
        'disk': ['disk.qcow2', 'attempt.json', 'prepared.json', 'OVMF_VARS.fd', 'tpm'],
        'seed': ['seed', 'seed.iso', 'vmlinuz', 'initrd', 'installer.iso', 'windows-media', 'prepared.json'],
        'screenshots': ['screenshots'],
        'logs': ['logs', 'steps.log', 'serial.log', 'qemu.log', 'events.jsonl', 'qemu-command.txt'],
    }
    if not targets:
        raise LabError('Choose disk, seed, screenshots, logs, keys, iso, out or all (all excludes ISO and shared keys)')
    if 'all' in targets:
        targets = sorted((set(targets) - {'all'}) | set(mapping) | {'out'})
    paths = []
    for target in targets:
        if target in mapping:
            paths.extend(lab.safe('work', lab.vm, p) for p in mapping[target])
        elif target == 'iso':
            paths += [lab.iso, lab.safe('iso', lab.iso.name + '.part')]
        elif target == 'keys':
            for vm in PROFILES:
                other = Lab(lab.root, vm)
                if other.pid():
                    raise LabError('Cannot remove shared SSH keys while any VM or operation is active')
            paths += [lab.safe('keys', f) for f in ('id_ed25519', 'id_ed25519.pub', 'known_hosts', 'known_hosts.old')]
        elif target == 'out':
            paths += [lab.safe('out', lab.vm + ext) for ext in ('.html', '.pdf')]
        else:
            raise LabError('Unknown clean target: ' + target)
    if lab.pid():
        raise LabError('Stop the owned VM before cleaning; evidence has been retained')
    paths = list(dict.fromkeys(paths))
    # Enumerate every file, rejecting symlinks before any mutation.
    selected = []
    for path in paths:
        if path.is_symlink():
            raise LabError(f'Refusing symlink: {path}')
        if path.is_dir():
            descendants = list(path.rglob('*'))
            if any(p.is_symlink() for p in descendants):
                raise LabError(f'Refusing symlink inside {path}')
            selected.extend(descendants)
        if path.exists():
            selected.append(path)
    selected = sorted(set(selected), key=lambda p: (len(p.parts), str(p)), reverse=True)
    for path in selected:
        print(f'Delete {"directory" if path.is_dir() else str(path.stat().st_size) + " bytes"}: {path}')
    if not selected:
        print('Nothing to delete.')
        return
    if dry:
        return
    confirm('Delete exactly the paths printed above?', yes)
    tpm_stop(lab)
    for path in selected:
        lab.safe(*path.relative_to(lab.root).parts)
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
