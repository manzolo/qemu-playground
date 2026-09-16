"""The menu is a thin wrapper around this CLI."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from .core import ACTIVE_LAB, Lab, LabError, PROFILES, busy, confirm, lock, run
from . import operations as ops
from .interface import menu_items, status
from .protocol import agent
from .report import report
from .screens import shot


def parser():
    p = argparse.ArgumentParser(description='An observable QEMU/KVM lab. Run ./lab without arguments for the menu.')
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    sub = p.add_subparsers(dest='action', required=True)
    def command(name, vm=False, help=None):
        item = sub.add_parser(name, help=help)
        if vm:
            item.add_argument('vm', choices=PROFILES)
        item.add_argument('--dry-run', action='store_true', help='Print actions without modifying files or launching processes')
        item.add_argument('--background', action='store_true', help='Detach, writing live output to the VM steps.log')
        item.add_argument('--foreground', action='store_true', help='Wait in this terminal (install/up detach by default)')
        return item
    d = command('doctor', help='Check host tools, KVM, RAM and storage')
    d.add_argument('--install', action='store_true')
    c = command('config', help='Initialize or show local configuration with sources')
    c.add_argument('operation', choices=['init', 'show'], default='show', nargs='?')
    i = command('iso', vm=True, help='Manage pinned installation media')
    i.add_argument('operation', choices=['download', 'verify', 'redownload', 'delete', 'import'])
    i.add_argument('--source', type=Path)
    i.add_argument('--yes', action='store_true')
    command('prepare', vm=True)
    for name in ('install', 'up'):
        c = command(name, vm=True)
        c.add_argument('--nudge', action='store_true', help='Explicitly send Enter once; mark the attempt assisted')
        c.add_argument('--keep-failed', action='store_true', default=True, help='Always enabled; failed disks are retained')
    command('start', vm=True)
    c = command('stop', vm=True)
    c.add_argument('--force', action='store_true')
    command('status')
    c = command('ssh', vm=True, help='Run a guest command and return its real exit status')
    c.add_argument('--timeout', type=int, default=120)
    c.add_argument('command', nargs=argparse.REMAINDER)
    c = command('agent', vm=True)
    c.add_argument('command', choices=['ping', 'info', 'osinfo', 'ip', 'shutdown'])
    c = command('console', vm=True)
    c.add_argument('--timeout', type=int, default=300)
    c = command('shot', vm=True)
    c.add_argument('--no-open', action='store_true')
    c.add_argument('--nudge', action='store_true')
    c = command('report', vm=True)
    c.add_argument('--pdf', action='store_true')
    c = command('clean', vm=True)
    c.add_argument('targets', nargs='+', choices=['disk', 'seed', 'screenshots', 'logs', 'keys', 'iso', 'out', 'all'])
    c.add_argument('--yes', action='store_true')
    for name in ('_menu', '_preview', '_execute'):
        c = command(name, vm=True)
        if name != '_menu':
            c.add_argument('item', type=int)
    return p


def background(lab, args, argv):
    if args.action == 'iso' and args.operation in ('delete', 'redownload') and not args.yes:
        print(f'Delete {lab.iso} and its partial download before {args.operation}.')
        confirm('Continue?')
        argv += ['--yes']
    lab.ensure()
    cmd = [sys.executable, '-u', '-m', 'playground.cli'] + [a for a in argv if a not in ('--background', '--foreground')] + ['--foreground']
    with lab.log.open('a', buffering=1) as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    lab.event(kind='background', command=shlex.join(cmd), outcome=f'worker PID {proc.pid}')
    print(f'Background worker PID {proc.pid}; log: {lab.log}\n$ tail -f {shlex.quote(str(lab.log))}')
    return 0


def dispatch(lab, args):
    dry = args.dry_run
    action = args.action
    if action == 'doctor':
        return ops.doctor(lab, args.install, dry)
    if action == 'config':
        ops.config(lab, args.operation, dry)
    elif action == 'status':
        status(lab)
    elif action == 'iso':
        ops.iso(lab, args.operation, source=args.source, yes=args.yes, dry=dry)
    elif action == 'prepare':
        ops.prepare(lab, dry)
    elif action == 'install':
        ops.install(lab, dry, args.nudge)
    elif action == 'start':
        ops.start(lab, dry=dry)
    elif action == 'stop':
        ops.stop(lab, force=args.force, dry=dry)
    elif action == 'ssh':
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        return ops.ssh(lab, command, dry=dry, timeout=args.timeout)
    elif action == 'agent':
        if dry:
            print(f'QGA {args.command} via {lab.qga}')
        else:
            print(json.dumps(agent(lab, args.command), indent=2))
    elif action == 'shot':
        if dry:
            print(f'QMP screendump via {lab.qmp}; convert PPM -> PNG; open={not args.no_open}; nudge={args.nudge}')
        else:
            shot(lab, open_image=not args.no_open, nudge=args.nudge)
    elif action == 'console':
        if lab.vm == 'windows-11':
            raise LabError('Windows has no useful interactive serial shell. Use shot, ssh or agent; serial.log contains setup tokens.')
        if not dry and not lab.serial.exists():
            raise LabError('No serial log yet; start or install the VM first')
        print(f'Read-only serial console; Ctrl-C returns. Follow ends after {args.timeout}s.')
        try:
            run(['tail', '-n', '80', '-f', lab.serial], timeout=args.timeout, dry=dry)
        except LabError as e:
            if not str(e).startswith('Timeout'):
                raise
    elif action == 'report':
        report(lab, args.pdf, dry)
    elif action == 'clean':
        ops.clean(lab, args.targets, yes=args.yes, dry=dry)
    elif action == 'up':
        if not dry and ops.doctor(lab):
            raise LabError('Prerequisite check failed; fix doctor findings before up')
        ops.config(lab, 'init', dry)
        if not dry:
            lab = Lab(lab.root, lab.vm)
            ACTIVE_LAB.set(lab)
        ops.iso(lab, 'download', dry=dry)
        ops.prepare(lab, dry)
        ops.install(lab, dry, args.nudge)
        ops.start(lab, dry=dry)
        if not dry:
            print('Waiting up to 300s for key-authenticated SSH on the installed guest.', flush=True)
            deadline = time.monotonic() + 300
            cmd = ops.ssh_command(lab, ['ver' if lab.vm == 'windows-11' else 'true'])
            while time.monotonic() < deadline:
                if not lab.pid():
                    raise LabError('Guest exited while waiting for SSH; inspect qemu.log and last screen')
                try:
                    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
                    ready = result.returncode == 0
                except subprocess.TimeoutExpired:
                    ready = False
                if ready:
                    lab.event(kind='ssh-ready', outcome='passed')
                    report(lab)
                    break
                time.sleep(3)
            else:
                shot(lab, caption='Timeout waiting for key-authenticated SSH')
                report(lab)
                raise LabError('Timeout after 300s waiting for key-authenticated SSH; VM retained')
    return 0


def main(argv=None):
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept --dry-run before or after the subcommand, without touching a guest
    # command following the explicit -- separator.
    end = argv.index('--') if '--' in argv else len(argv)
    dry = '--dry-run' in argv[:end]
    argv = [a for i, a in enumerate(argv) if not (i < end and a == '--dry-run')]
    args = parser().parse_args(argv)
    args.dry_run = dry
    try:
        lab = Lab(args.root, getattr(args, 'vm', PROFILES[0]))
        if args.action in ('_menu', '_preview', '_execute'):
            items = menu_items(lab)
            if args.action == '_menu':
                for item in items:
                    print(f'{item["id"]}\t{item["label"]} {item["state"]}')
                return 0
            if not 0 <= args.item < len(items):
                raise LabError('Unknown menu item')
            item = items[args.item]
            cmd = [str(lab.root / 'lab')] + item['command']
            print(item['label'] + ' ' + item['state'], flush=True)
            print('$ ' + shlex.join(cmd), flush=True)
            if args.action == '_preview':
                print(f'\nLog: {lab.log}\n\nCtrl-R refresh · Ctrl-C returns · profile: {lab.vm}')
                return 0
            if not item['enabled']:
                raise LabError(item['state'])
            suffix = ['--background'] if item['background'] else []
            return main(['--root', str(lab.root)] + item['command'] + suffix)
        if args.action in ('install', 'up') and not args.foreground:
            args.background = True
        if args.background and not dry:
            if args.action == 'ssh':
                raise LabError('SSH runs in foreground so its actual exit code can be returned')
            return background(lab, args, argv)
        audited = args.action not in ('status', 'doctor', 'config') and not dry
        mutation = args.action in ('iso', 'prepare', 'install', 'up', 'start', 'clean')
        start = time.monotonic()
        context = ACTIVE_LAB.set(lab if audited else None)
        if audited:
            lab.event(kind='command-start', command=shlex.join(['./lab'] + argv), outcome='running')
        try:
            if mutation and not dry:
                lab.ensure()
                with contextlib.ExitStack() as guards:
                    profiles = PROFILES if args.action == 'clean' and 'keys' in args.targets else [lab.vm]
                    for vm in profiles:
                        guards.enter_context(lock(Lab(lab.root, vm).oplock, timeout=.1))
                    code = dispatch(lab, args)
            else:
                code = dispatch(lab, args)
        except BaseException as e:
            if audited:
                lab.event(kind='command-end', command=shlex.join(['./lab'] + argv), outcome='failed: ' + str(e), duration=round(time.monotonic()-start, 2))
            raise
        finally:
            ACTIVE_LAB.reset(context)
        if audited:
            lab.event(kind='command-end', command=shlex.join(['./lab'] + argv), outcome='passed' if not code else f'exit {code}', duration=round(time.monotonic()-start, 2))
        if audited and args.action in ('install', 'up'):
            report(Lab(lab.root, lab.vm))
        return code or 0
    except (LabError, OSError, subprocess.TimeoutExpired, ValueError) as e:
        print(f'ERROR: {e}', file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print('\nDetached workers and VMs continue. Use status, shot or stop.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
