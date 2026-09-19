"""Isolated contract tests: never read the developer's .env, keys or VM files."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib

from playground.cli import main
from playground.core import DEFAULTS, Lab, LabError, atomic, lab_password, lock, records, sha256
from playground.interface import header, menu_items
from playground.operations import clean, install, prepare, qemu_command, recover, ssh, ssh_command
from playground.protocol import JsonSocket, agent, qmp
from playground.report import report
from playground.screens import png, ppm, shot
from playground.seeds import lubuntu_seed, windows_seed

SOURCE = Path(__file__).resolve().parents[1]


class LabCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qpl-test-')
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / 'profiles', self.root / 'profiles')
        shutil.copytree(SOURCE / 'templates', self.root / 'templates')
        shutil.copyfile(SOURCE / '.env.example', self.root / '.env.example')
        self.lab = Lab(self.root)
        self.addCleanup(self.temp.cleanup)

    def invoke(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(['--root', str(self.root), *args])
        return code, out.getvalue()

    def test_up_dry_run_has_no_side_effects_and_exact_qemu(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        code, output = self.invoke('up', self.lab.vm, '--dry-run')
        self.assertEqual(code, 0, output)
        self.assertIn('qemu-system-x86_64', output)
        self.assertIn('autoinstall ds=nocloud', output)
        self.assertIn('127.0.0.1:2400-:22', output)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_windows_dry_run_without_any_media(self):
        code, output = self.invoke('install', 'windows-11', '--dry-run')
        self.assertEqual(code, 0, output)
        self.assertIn('tpm-tis', output)
        self.assertIn('isa-serial', output)
        self.assertNotIn('-kernel', output)
        self.assertFalse((self.root / 'work').exists())

    def test_env_data_is_never_executed_and_password_is_hidden(self):
        marker = self.root / 'injected'
        atomic(self.root / '.env', 'LAB_PASSWORD=$(touch ' + str(marker) + ')\n')
        code, output = self.invoke('config', 'show')
        self.assertEqual(code, 0)
        self.assertNotIn('touch', output)
        self.assertFalse(marker.exists())
        self.assertIn('(default)', output)
        self.assertIn('(.env)', output)

    def test_config_init_private_and_not_overwritten(self):
        self.assertEqual(self.invoke('config', 'init')[0], 0)
        path = self.root / '.env'
        original = path.read_text()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(Lab(self.root).cfg['LAB_PASSWORD'])
        self.invoke('config', 'init')
        self.assertEqual(path.read_text(), original)

    def test_checksum_cache_is_invalidated_on_change(self):
        self.lab.iso.parent.mkdir()
        self.lab.iso.write_bytes(b'trusted fixture')
        self.lab.profile['sha256'] = sha256(self.lab.iso)
        self.assertEqual(self.lab.iso_state(full=True), 'verified')
        self.assertEqual(self.lab.iso_state(), 'verified')
        self.lab.iso.write_bytes(b'tampered fixture')
        self.assertNotEqual(self.lab.iso_state(), 'verified')
        self.assertIn('MISMATCH', self.lab.iso_state(full=True))

    def test_wrong_iso_cannot_prepare_and_is_not_deleted(self):
        self.lab.iso.parent.mkdir()
        self.lab.iso.write_bytes(b'wrong')
        with self.assertRaises(LabError):
            prepare(self.lab)
        self.assertTrue(self.lab.iso.exists())
        self.assertFalse(self.lab.disk.exists())

    def test_pid_rejects_real_unrelated_process(self):
        self.lab.ensure()
        self.lab.pidfile.write_text(str(os.getpid()))
        self.assertIsNone(self.lab.pid())
        with self.assertRaises(LabError):
            qmp(self.lab, 'system_powerdown')

    def test_process_identification_requires_all_owned_paths(self):
        self.lab.ensure()
        self.lab.pidfile.write_text('12345')
        args = qemu_command(self.lab)
        with patch.object(Path, 'read_bytes', return_value='\0'.join(args).encode()):
            self.assertEqual(self.lab.pid(), 12345)
        args[args.index('-qmp') + 1] = 'unix:/tmp/another-lab.sock,server=on,wait=off'
        with patch.object(Path, 'read_bytes', return_value='\0'.join(args).encode()):
            self.assertIsNone(self.lab.pid())

    def test_seed_schema_and_flush_before_token(self):
        cfg = dict(DEFAULTS, LAB_PASSWORD='fixture<&"password')
        text = lubuntu_seed(cfg, 'ssh-ed25519 TEST_ONLY', '$6$TEST_ONLY', 'fixture-token')
        data = json.loads(text.split('\n', 1)[1])['autoinstall']
        self.assertFalse(data['ssh']['allow-pw'])
        self.assertEqual(data['shutdown'], 'poweroff')
        completion = data['late-commands'][-1][-1]
        self.assertLess(completion.index('sync'), completion.index('blockdev'))
        self.assertLess(completion.index('blockdev'), completion.index('LAB_OK_'))
        self.assertIn('LAB_FAIL_fixture-token', data['error-commands'][0][-1])
        xml, script = windows_seed(cfg, 'ssh-ed25519 TEST_ONLY', 'fixture-token',
                                    (self.root / 'templates/windows-setup.ps1').read_text())
        root = ET.fromstring(xml)
        ns = {'u': 'urn:schemas-microsoft-com:unattend'}
        self.assertEqual(root.find('.//u:LocalAccount/u:Password/u:Value', ns).text, cfg['LAB_PASSWORD'])
        self.assertIn('COM2', script)
        self.assertIn('PasswordAuthentication no', script)
        self.assertLess(script.index('Write-VolumeCache'), script.index("Emit 'LAB_OK_"))
        self.assertLess(script.index("Emit 'LAB_OK_"), script.index('& shutdown.exe'))
        self.assertIn('LAB_FAIL_fixture-token', script)

    def _stop_with_unresponsive_guest(self, vm):
        """Every graceful channel fails; return the mocked ssh() for inspection."""
        import itertools
        from playground import operations
        lab = Lab(self.root, vm)
        lab.safe('keys').mkdir(exist_ok=True)
        lab.safe('keys', 'id_ed25519').write_text('TEST ONLY')
        with patch.object(Lab, 'pid', return_value=4242), \
             patch('playground.operations.qmp'), \
             patch('playground.operations.agent', side_effect=LabError('no agent')), \
             patch('playground.operations.ssh') as remote, \
             patch('playground.operations.time.sleep'), \
             patch('playground.operations.time.monotonic', side_effect=itertools.count(0, 1000)):
            with self.assertRaisesRegex(LabError, 'stop --force'):
                operations.stop(lab)
        return remote

    def test_windows_stop_asks_over_ssh_before_any_signal(self):
        remote = self._stop_with_unresponsive_guest('windows-11')
        remote.assert_called_once()
        self.assertIn('shutdown /s', remote.call_args.args[1][0])

    def test_linux_stop_never_uses_ssh_to_power_off(self):
        # ACPI powers Linux off in seconds and the lab user has no passwordless sudo.
        self._stop_with_unresponsive_guest('lubuntu-26.04').assert_not_called()

    def test_specialize_runs_nothing_and_oobe_declares_locales(self):
        """A non-zero specialize command stops Setup on a modal dialog forever."""
        template = (self.root / 'templates/windows-setup.ps1').read_text()
        xml, _ = windows_seed(dict(DEFAULTS, LAB_PASSWORD='fixture'),
                              'ssh-ed25519 TEST_ONLY', 'fixture-token', template)
        root = ET.fromstring(xml)
        ns = {'u': 'urn:schemas-microsoft-com:unattend'}
        passes = {s.get('pass'): s for s in root.findall('u:settings', ns)}
        self.assertEqual(passes['specialize'].findall('.//u:RunSynchronous', ns), [])
        self.assertIsNone(passes['specialize'].find('.//u:RunSynchronousCommand', ns))
        # International-Core in oobeSystem too, or OOBE stops on region/keyboard.
        for name in ('specialize', 'oobeSystem'):
            components = [c.get('name') for c in passes[name].findall('u:component', ns)]
            self.assertIn('Microsoft-Windows-International-Core', components, name)
        flags = {e.tag.split('}')[1] for e in passes['oobeSystem'].find('.//u:OOBE', ns)}
        self.assertLessEqual({'HideEULAPage', 'HideLocalAccountScreen',
                              'HideOnlineAccountScreens', 'HideWirelessSetupInOOBE'}, flags)
        # RunOnce silently drops FirstLogonCommands longer than MAX_PATH, and the
        # script is read from the seed CD rather than a copy made earlier.
        logon = passes['oobeSystem'].find('.//u:FirstLogonCommands/u:SynchronousCommand/u:CommandLine', ns).text
        self.assertLess(len(logon), 260, logon)
        self.assertIn('setup.ps1', logon)
        self.assertNotIn('ProgramData', logon)

    def test_guest_gates_agent_msi_on_pinned_digest_not_signature(self):
        digest = 'ab' * 32
        cfg = dict(DEFAULTS, LAB_PASSWORD='fixture', LAB_QGA_SHA256=digest.upper())
        template = (self.root / 'templates/windows-setup.ps1').read_text()
        _, script = windows_seed(cfg, 'ssh-ed25519 TEST_ONLY', 'fixture-token', template)
        # The digest is rendered in lowercase and compared before msiexec runs.
        self.assertIn("$expected = '" + digest + "'", script)
        self.assertNotIn('@QGA_SHA256@', script)
        self.assertLess(script.index('SHA-256 mismatch'), script.index('msiexec.exe'))
        # Authenticode is reported, never a hard gate: the distributor ships unsigned.
        self.assertNotIn("throw 'QGA MSI signature is not valid'", script)
        # An unrendered or absent pin must fail closed rather than skip the check.
        _, blank = windows_seed(dict(cfg, LAB_QGA_SHA256=''), 'ssh-ed25519 TEST_ONLY', 'fixture-token', template)
        self.assertIn('No pinned QGA MSI digest', blank)
        self.assertIn("$expected = ''", blank)

    def test_ssh_isolated_and_windows_has_no_posix_shell(self):
        for vm in ('lubuntu-26.04', 'windows-11'):
            lab = Lab(self.root, vm)
            args = ssh_command(lab, ['echo', 'a b'])
            self.assertIn('BatchMode=yes', args)
            self.assertIn('IdentitiesOnly=yes', args)
            self.assertIn('GlobalKnownHostsFile=/dev/null', args)
            self.assertIn(str(self.root / 'keys/id_ed25519'), args)
            if vm == 'windows-11':
                self.assertIn('cmd.exe', args[-1])
                self.assertNotIn('sh -lc', args[-1])

    def test_ssh_preserves_guest_exit_code(self):
        self.lab.safe('keys').mkdir()
        self.lab.safe('keys', 'id_ed25519').write_text('TEST ONLY')
        with patch.object(Lab, 'pid', return_value=123), \
             patch.object(Path, 'read_bytes', return_value='\0'.join(qemu_command(self.lab)).encode()), \
             patch('playground.operations.subprocess.run') as proc:
            proc.return_value.returncode = 42
            self.assertEqual(ssh(self.lab, ['false']), 42)

    def test_ssh_rejects_changed_port_while_guest_is_running(self):
        runtime = qemu_command(self.lab)
        self.lab.port += 10
        with patch.object(Lab, 'pid', return_value=123), \
             patch.object(Path, 'read_bytes', return_value='\0'.join(runtime).encode()), \
             patch('playground.operations.subprocess.run') as proc:
            with self.assertRaisesRegex(LabError, 'SSH port differs'):
                ssh(self.lab, ['true'])
        proc.assert_not_called()

    def test_clean_all_excludes_iso_and_shared_keys(self):
        self.lab.ensure()
        self.lab.iso.parent.mkdir()
        self.lab.iso.write_bytes(b'expensive')
        keys = self.lab.safe('keys')
        keys.mkdir()
        (keys / 'id_ed25519').write_text('TEST ONLY')
        self.lab.disk.write_bytes(b'failed evidence')
        clean(self.lab, ['all'], dry=True)
        self.assertTrue(self.lab.disk.exists())
        clean(self.lab, ['all'], yes=True)
        self.assertFalse(self.lab.disk.exists())
        self.assertTrue(self.lab.iso.exists())
        self.assertTrue((keys / 'id_ed25519').exists())

    def test_clean_rejects_symlink_before_deleting_anything(self):
        self.lab.ensure()
        self.lab.disk.write_bytes(b'evidence')
        outside = self.root / 'precious'
        outside.mkdir()
        (outside / 'data').write_text('keep')
        (self.lab.work / 'seed').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(LabError):
            clean(self.lab, ['disk', 'seed'], yes=True)
        self.assertTrue(self.lab.disk.exists())
        self.assertEqual((outside / 'data').read_text(), 'keep')

    def test_lock_timeout_has_context(self):
        self.lab.ensure()
        with lock(self.lab.oplock):
            with self.assertRaisesRegex(LabError, 'operation.lock'):
                with lock(self.lab.oplock, timeout=.01):
                    pass

    def test_disabled_menu_entries_stay_visible(self):
        items = menu_items(Lab(self.root, 'windows-11'))
        self.assertIn('prerequisites', items[0]['label'])
        install_item = next(i for i in items if i['command'][0] == 'install')
        self.assertFalse(install_item['enabled'])
        self.assertIn('missing ISO', install_item['reason'])
        self.assertTrue(any(i['command'][0] == 'start' for i in items))

    def test_dry_runs_stay_printable_while_a_vm_is_running(self):
        with patch.object(Lab, 'pid', return_value=4242):
            self.assertEqual(self.invoke('iso', 'lubuntu-26.04', 'verify', '--dry-run')[0], 0)
            self.assertEqual(self.invoke('up', 'lubuntu-26.04', '--dry-run')[0], 0)
            for action in ('prepare', 'install'):
                code, output = self.invoke(action, 'windows-11', '--dry-run')
                self.assertEqual(code, 0, output)
                self.assertIn('qemu-img' if action == 'prepare' else 'qemu-system-x86_64', output)
            with self.assertRaisesRegex(LabError, 'Stop the VM'):
                prepare(Lab(self.root, 'windows-11'))

    def test_graphical_console_is_on_by_default_and_can_be_turned_off(self):
        from playground.operations import view
        linux, windows = Lab(self.root), Lab(self.root, 'windows-11')
        self.assertEqual(linux.vnc, 5940)        # clear of libvirt, which starts at 5900
        self.assertEqual(windows.vnc, 5941)      # same +1 rule as the SSH port
        self.assertIn('127.0.0.1:41', qemu_command(windows))
        atomic(self.root / '.env', 'LAB_VNC_PORT=0\n')
        self.assertNotIn('-vnc', qemu_command(Lab(self.root)))
        with self.assertRaisesRegex(LabError, 'LAB_VNC_PORT'):
            view(Lab(self.root))
        atomic(self.root / '.env', 'LAB_VNC_PORT=70000\n')
        with self.assertRaisesRegex(LabError, 'LAB_VNC_PORT'):
            Lab(self.root)

    def history(self, *rows):
        atomic(self.lab.work / 'events.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))

    def test_report_separates_an_exposed_console_from_a_used_one(self):
        self.lab.ensure()
        # A console is exposed when the VM starts, so it precedes the verdict the
        # attempt ends with, and the attempt's span is what the event records.
        done = {'kind': 'installation', 'time': 1000, 'duration': 100, 'outcome': 'passed (unattended)'}
        exposed = {'kind': 'console', 'time': 950, 'outcome': 'graphical console exposed'}
        self.history(done)
        self.assertNotIn('graphical console', report(self.lab).read_text())
        self.history(exposed, done)
        text = report(self.lab).read_text()
        self.assertIn('no client connected', text)
        self.assertNotIn('not provably unattended', text)   # exposed is not used
        self.history(exposed, {'kind': 'console-client', 'time': 960, 'outcome': 'someone connected'}, done)
        self.assertIn('not provably unattended', report(self.lab).read_text())

    def test_one_attempts_console_does_not_qualify_the_next_attempts_verdict(self):
        """The verdict is about one attempt, so only that attempt may weaken it.

        Reading the whole history made a console opened during one run contradict
        every run after it, and the previous verdict is the wrong boundary: up()
        boots the installed guest as soon as it records one, and that boot's own
        console event lands a fraction of a second on the far side.
        """
        self.lab.ensure()
        self.history(
            {'kind': 'console', 'time': 100, 'outcome': 'graphical console exposed'},
            {'kind': 'console-client', 'time': 150, 'outcome': 'someone connected'},
            {'kind': 'intervention', 'time': 160, 'outcome': 'Enter sent'},
            {'kind': 'installation', 'time': 200, 'duration': 120, 'outcome': 'passed (assisted)'},
            {'kind': 'console', 'time': 200.4, 'outcome': 'graphical console exposed'},
            {'kind': 'installation', 'time': 900, 'duration': 300, 'outcome': 'passed (unattended)'})
        text = report(self.lab).read_text()
        self.assertIn('attempt 2', text)
        self.assertNotIn('not provably unattended', text)
        self.assertNotIn('no client connected', text)
        self.assertNotIn('explicit keyboard intervention', text)
        # The earlier attempt is still in the report; it is just not the verdict.
        self.assertIn('passed (assisted)', text)

    def test_report_can_hand_the_artefact_to_the_desktop(self):
        self.lab.ensure()
        with patch('playground.report.open_file') as opener:
            report(self.lab)
            opener.assert_not_called()          # never on the internal calls
            report(self.lab, open_after=True)
        self.assertEqual(opener.call_args.args[0].suffix, '.html')
        # The menu offers it, so a produced report is one keystroke from being read.
        entries = [i['command'] for i in menu_items(self.lab) if i['command'][0] == 'report']
        self.assertTrue(all('--open' in c for c in entries), entries)

    def test_doctor_entry_reflects_host_readiness(self):
        """It used to be hard-coded [todo] even on a host that passed every check."""
        for ready, expected in ((True, 'done'), (False, 'todo')):
            with patch('playground.interface.readiness', return_value={'ready': ready}):
                entry = menu_items(Lab(self.root))[0]
            self.assertEqual(entry['command'][0], 'doctor')
            self.assertEqual(entry['status'], expected)

    def test_shipped_example_matches_the_built_in_defaults(self):
        """They are two copies of the same table, and they drifted silently before."""
        example = {}
        for line in (SOURCE / '.env.example').read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                key, _, value = line.partition('=')
                example[key.strip()] = value.strip()
        self.assertEqual(set(example), set(DEFAULTS))
        self.assertEqual({k: v for k, v in example.items() if k != 'LAB_PASSWORD'},
                         {k: v for k, v in DEFAULTS.items() if k != 'LAB_PASSWORD'})

    def test_bare_ssh_opens_an_interactive_session(self):
        args = ssh_command(self.lab, [])
        self.assertIn('-t', args)                       # a real tty in the guest
        self.assertEqual(args[-1], 'labuser@127.0.0.1')  # nothing appended to run
        self.assertNotIn('sh -lc', ' '.join(args))
        self.lab.safe('keys').mkdir()
        self.lab.safe('keys', 'id_ed25519').write_text('TEST ONLY')
        with patch.object(Lab, 'pid', return_value=123), \
             patch.object(Path, 'read_bytes', return_value='\0'.join(qemu_command(self.lab)).encode()), \
             patch('playground.operations.subprocess.run') as proc:
            proc.return_value.returncode = 0
            ssh(self.lab, [], timeout=120)
        # An interactive shell must outlive the one-shot command timeout.
        self.assertIsNone(proc.call_args.kwargs['timeout'])

    def test_ssh_state_is_probed_once_and_both_entries_agree(self):
        """Two probes let one screen contradict itself, and a tight one made it lie."""
        with patch.object(Lab, 'pid', return_value=4242), \
             patch.object(Lab, 'ssh_banner', return_value=True) as probe:
            entries = [i for i in menu_items(Lab(self.root)) if i['command'][0] == 'ssh']
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(len(entries), 2)
        self.assertEqual({i['enabled'] for i in entries}, {True})

    def test_console_gets_an_absolute_pointer_and_audio_stays_opt_in(self):
        args = qemu_command(Lab(self.root))
        # VNC sends absolute coordinates; a PS/2 mouse turns them into a drifting
        # pointer, so the console always brings a tablet.
        self.assertIn('usb-tablet,bus=usb.0', args)
        self.assertNotIn('ich9-intel-hda', args)      # no sound card unless asked
        atomic(self.root / '.env', 'LAB_VNC_PORT=0\n')
        self.assertNotIn('usb-tablet,bus=usb.0', qemu_command(Lab(self.root)))
        atomic(self.root / '.env', 'LAB_AUDIO=pipewire\n')
        args = qemu_command(Lab(self.root))
        self.assertIn('pipewire,id=snd0', args)
        self.assertIn('hda-duplex,audiodev=snd0', args)
        atomic(self.root / '.env', 'LAB_AUDIO=nonsense\n')
        with self.assertRaisesRegex(LabError, 'LAB_AUDIO'):
            Lab(self.root)

    def test_lubuntu_desktop_is_required_even_with_legacy_config(self):
        for flag in ('0', '1'):
            atomic(self.root / '.env', f'LAB_PASSWORD=fixture\nLAB_DESKTOP={flag}\n')
            cfg = Lab(self.root).cfg
            self.assertNotIn('LAB_DESKTOP', cfg)
            data = json.loads(lubuntu_seed(cfg, 'k', '$6$h', 'tok').split('\n', 1)[1])['autoinstall']
            steps = [c[-1] for c in data['late-commands']]
            self.assertIn('lubuntu-desktop', data['packages'])
            self.assertIn('qemu-guest-agent', data['packages'])
            self.assertEqual(data['apt']['fallback'], 'abort')
            self.assertTrue(any('enable sddm.service' in s for s in steps))
            self.assertTrue(any('set-default graphical.target' in s for s in steps))
            check = next(s for s in steps if 'dpkg-query' in s)
            self.assertIn('lxqt-session', check)
            self.assertNotIn('||', check)
            self.assertIn('LAB_OK_tok', steps[-1])

    def test_up_waits_for_a_running_lubuntu_desktop_before_recording_success(self):
        from argparse import Namespace
        from playground.cli import dispatch
        from playground.core import ACTIVE_LAB
        from playground.desktop import lubuntu_check
        from subprocess import CompletedProcess
        self.lab.ensure()
        context = ACTIVE_LAB.set(None)
        self.addCleanup(ACTIVE_LAB.reset, context)
        with contextlib.ExitStack() as stack:
            for name in ('config', 'iso', 'prepare', 'install', 'start'):
                stack.enter_context(patch('playground.cli.ops.' + name))
            stack.enter_context(patch('playground.cli.ops.doctor', return_value=0))
            stack.enter_context(patch.object(Lab, 'pid', return_value=123))
            stack.enter_context(patch('playground.cli.report'))
            capture = stack.enter_context(patch('playground.cli.shot'))
            sleep = stack.enter_context(patch('playground.cli.time.sleep'))
            proc = stack.enter_context(patch('playground.cli.subprocess.run', side_effect=[
                CompletedProcess([], 1), CompletedProcess([], 0)]))
            dispatch(self.lab, Namespace(action='up', dry_run=False, nudge=False))
        self.assertEqual(proc.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertIn(lubuntu_check(self.lab.cfg, running=True), shlex.split(proc.call_args.args[0][-1]))
        events = records(self.lab.work / 'events.jsonl')
        self.assertEqual([e['kind'] for e in events], ['ssh-ready', 'desktop-ready'])
        capture.assert_called_once()

    def test_sddm_drop_in_carries_the_greeter_keyboard_and_optional_autologin(self):
        """SDDM does not read /etc/default/keyboard, so the greeter needs telling."""
        import base64
        from playground.desktop import SDDM_CONF, XSETUP, lubuntu_check, sddm_conf, xsetup_script
        cfg = dict(DEFAULTS, LAB_PASSWORD='fixture', LAB_KEYBOARD='it', LAB_USER='labuser')
        steps = [c[-1] for c in json.loads(
            lubuntu_seed(cfg, 'k', '$6$h', 'tok').split('\n', 1)[1])['autoinstall']['late-commands']]
        written = {}
        for step in steps:
            if 'base64 -d' in step:
                blob = step.split('echo ', 1)[1].split(' |', 1)[0]
                written[step.rsplit('> ', 1)[1].split(' ')[0]] = base64.b64decode(blob).decode()
        self.assertEqual(written[XSETUP], xsetup_script(cfg))
        self.assertEqual(written[SDDM_CONF], sddm_conf(cfg))
        self.assertIn('-layout it', written[XSETUP])
        self.assertIn('User=labuser', written[SDDM_CONF])
        self.assertIn('Session=Lubuntu.desktop', written[SDDM_CONF])
        # Both files are written before the check that gates the token, and the
        # check is the last thing before it.
        order = [i for i, s in enumerate(steps) if 'base64 -d' in s or 'dpkg-query' in s]
        self.assertEqual(order, sorted(order))
        self.assertIn('LAB_OK_tok', steps[-1])
        # The greeter script is useless without setxkbmap, so its absence must fail
        # rather than leave the greeter quietly on another layout.
        check = lubuntu_check(cfg)
        self.assertIn('command -v setxkbmap', check)
        self.assertIn(f'grep -qx "DisplayCommand={XSETUP}" {SDDM_CONF}', check)
        self.assertNotIn('||', check)

    def test_autologin_is_configurable_and_checked_only_when_on(self):
        from playground.desktop import lubuntu_check, sddm_conf
        on = dict(DEFAULTS, LAB_PASSWORD='fixture', LAB_AUTOLOGIN='1')
        off = dict(on, LAB_AUTOLOGIN='0')
        self.assertIn('[Autologin]', sddm_conf(on))
        self.assertNotIn('[Autologin]', sddm_conf(off))
        # Both still configure the greeter's keyboard: turning autologin off is
        # exactly when the greeter is the screen you type the password at.
        for cfg in (on, off):
            self.assertIn('DisplayCommand=', sddm_conf(cfg))
            self.assertIn('-layout', lubuntu_check(cfg))
        self.assertIn('User=labuser', lubuntu_check(on))
        self.assertNotIn('User=labuser', lubuntu_check(off))
        # An SSH check makes a session for the user too, so "a session exists" would
        # pass without any autologin; the active session on seat0 is the real claim.
        running = lubuntu_check(on, running=True)
        self.assertIn('show-seat seat0 -p ActiveSession', running)
        self.assertNotIn('seat0', lubuntu_check(off, running=True))
        atomic(self.root / '.env', 'LAB_AUTOLOGIN=2\n')
        with self.assertRaisesRegex(LabError, 'LAB_AUTOLOGIN'):
            Lab(self.root)

    def test_the_session_locale_is_set_in_the_only_layer_that_wins(self):
        """Three correct system files still lost to systemd's LANG=C.UTF-8."""
        from playground.desktop import SESSION_CONF, lubuntu_check, session_conf
        import base64
        cfg = dict(DEFAULTS, LAB_PASSWORD='fixture', LAB_LOCALE='it_IT.UTF-8')
        steps = [c[-1] for c in json.loads(
            lubuntu_seed(cfg, 'k', '$6$h', 'tok').split('\n', 1)[1])['autoinstall']['late-commands']]
        written = next(s for s in steps if SESSION_CONF in s and 'base64 -d' in s)
        blob = written.split('echo ', 1)[1].split(' |', 1)[0]
        self.assertEqual(base64.b64decode(blob).decode(), session_conf(cfg))
        self.assertIn('[Environment]', session_conf(cfg))
        check = lubuntu_check(cfg, running=True)
        self.assertIn(f'grep -qx "LANG=it_IT.UTF-8" {SESSION_CONF}', check)
        # The file being right is not the session having read it, and that gap is
        # the entire defect, so the running check reads the session's environment.
        self.assertIn('pgrep -u labuser -x lxqt-session', check)
        self.assertIn('"/environ', check)
        # There is no session to read before the guest is up, or when nothing logs in.
        self.assertNotIn('pgrep', lubuntu_check(cfg))
        self.assertNotIn('pgrep', lubuntu_check(dict(cfg, LAB_AUTOLOGIN='0'), running=True))
        # The file is still written either way; only the live check needs a session.
        self.assertIn(SESSION_CONF, lubuntu_check(dict(cfg, LAB_AUTOLOGIN='0')))
        # It follows LAB_LOCALE rather than being pinned to one language.
        self.assertIn('LANG=fr_FR.UTF-8', session_conf(dict(cfg, LAB_LOCALE='fr_FR.UTF-8')))

    def test_recover_reads_a_lost_verdict_out_of_an_archived_serial_log(self):
        """QEMU truncates a file: log on every boot, so the token may be archived."""
        self.lab.ensure()
        atomic(self.lab.work / 'attempt.json', json.dumps({'token': 'tok', 'start': 100}))
        (self.lab.work / 'logs').mkdir()
        (self.lab.work / 'logs' / 'serial-1.log').write_text('boot\nLAB_OK_tok\n')
        self.lab.serial.write_text('a later boot with no token at all\n')
        with patch.object(Lab, 'pid', return_value=None), patch('playground.report.report'):
            outcome = recover(self.lab)
        self.assertTrue(outcome.startswith('passed (unattended'))
        # The one thing recovery cannot see is whether QEMU left on its own, so the
        # verdict has to say so instead of borrowing the live one's wording.
        self.assertIn('recovered', outcome)
        self.assertIn('exit not observed', outcome)
        event = [e for e in records(self.lab.work / 'events.jsonl') if e['kind'] == 'installation'][-1]
        self.assertEqual(event['outcome'], outcome)
        self.assertIn('serial-1.log', event['detail'])
        # A failure token is recovered as a failure, not as an absence of evidence.
        atomic(self.lab.work / 'events.jsonl', '')
        (self.lab.work / 'logs' / 'serial-1.log').write_text('LAB_FAIL_tok\n')
        with patch.object(Lab, 'pid', return_value=None), patch('playground.report.report'):
            self.assertTrue(recover(self.lab).startswith('failed'))

    def test_recover_refuses_what_it_cannot_honestly_decide(self):
        self.lab.ensure()
        with self.assertRaisesRegex(LabError, 'No installation attempt'):
            recover(self.lab)
        atomic(self.lab.work / 'attempt.json', json.dumps({'token': 'tok', 'start': 100}))
        with patch.object(Lab, 'pid', return_value=4242):
            with self.assertRaisesRegex(LabError, 'still running'):
                recover(self.lab)
        # No token anywhere is a failure, not a pass by default.
        self.lab.serial.write_text('nothing useful\n')
        with patch.object(Lab, 'pid', return_value=None), patch('playground.report.report'):
            self.assertIn('no completion token', recover(self.lab))
        # And a settled attempt is never quietly overwritten with a second verdict.
        with patch.object(Lab, 'pid', return_value=None):
            with self.assertRaisesRegex(LabError, 'already has a verdict'):
                recover(self.lab)

    def test_menu_ends_with_a_way_out(self):
        items = menu_items(self.lab)
        self.assertEqual(items[-1]['command'], ['_quit'])
        self.assertTrue(items[-1]['enabled'])            # always available
        last = str(len(items) - 1)
        self.assertEqual(self.invoke('_execute', self.lab.vm, last)[0], 64)
        self.assertEqual(self.invoke('_preview', self.lab.vm, last)[0], 0)

    def test_generated_password_can_be_typed_at_a_console(self):
        seen = {lab_password() for _ in range(50)}
        self.assertGreater(len(seen), 25)                # not a fixed string
        for password in seen:
            self.assertLessEqual(len(password), 12, password)
            self.assertTrue(password.isascii() and ' ' not in password, password)
            # Upper, lower and digit: acceptable to Windows even with complexity on.
            for kind in (str.isupper, str.islower, str.isdigit):
                self.assertTrue(any(kind(ch) for ch in password), password)

    def test_header_names_the_condition_that_blocks_the_entries(self):
        """Fifteen entries blocked by one condition must not leave it unstated."""
        with patch.object(Lab, 'pid', return_value=4242):
            running = header(Lab(self.root))
            blocked = [i for i in menu_items(Lab(self.root)) if not i['enabled']]
        self.assertIn('VM running', running)
        self.assertIn('ISO missing', running)
        self.assertTrue(any('running' in i['reason'] for i in blocked), blocked)
        self.assertIn('VM stopped', header(Lab(self.root)))

    def test_blocked_entry_states_its_reason_exactly_once(self):
        items = menu_items(Lab(self.root, 'windows-11'))
        index = next(n for n, i in enumerate(items) if not i['enabled'])
        code, output = self.invoke('_execute', 'windows-11', str(index))
        self.assertEqual(code, 1, output)
        self.assertEqual(output.count(items[index]['reason']), 1, output)
        self.assertNotIn('$ ', output)   # no command line for something that will not run

    def test_menu_rows_align_and_keep_the_reason_out_of_the_label(self):
        items = menu_items(Lab(self.root, 'windows-11'))
        for item in items:
            self.assertTrue(item['row'].startswith(item['group']))
            self.assertNotRegex(item['row'], r'\[(done|todo|blocked)\]')
        # Every row starts its label at the same column, so nothing needs truncating.
        columns = {i['row'].index(i['label']) for i in items}
        self.assertEqual(len(columns), 1, sorted(i['row'] for i in items))
        blocked = next(i for i in items if not i['enabled'])
        self.assertNotIn(blocked['reason'], blocked['row'])
        self.assertIn(blocked['reason'], blocked['state'])

    def test_copyable_command_matches_preview_and_background_execution(self):
        # A path containing spaces and shell syntax must survive a copy/paste.
        root = self.root / 'lab space $(nothing)'
        root.mkdir()
        shutil.copytree(self.root / 'profiles', root / 'profiles')
        lab = Lab(root)
        items = menu_items(lab)
        for action in ('iso', 'ssh', '_quit'):
            item = next(i for i in items if i['command'][0] == action)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(['--root', str(root), '_command', lab.vm, item['id']])
            self.assertEqual(code, 0)
            line = out.getvalue().rstrip('\n')
            if action == '_quit':
                self.assertEqual(line, '')
                continue
            expected = [str(root / 'lab')] + item['command']
            if item['background']:
                expected.append('--background')
            self.assertEqual(shlex.split(line), expected)
            self.assertNotIn('\n', line)
            preview = io.StringIO()
            with contextlib.redirect_stdout(preview):
                self.assertEqual(main(['--root', str(root), '_preview', lab.vm, item['id']]), 0)
            self.assertEqual(preview.getvalue().splitlines()[0], line)
        self.assertFalse((root / 'work').exists())

    def test_language_flag_defaults_to_english_and_translates_menu_only(self):
        self.assertEqual(Lab(self.root).cfg['LAB_LANG'], 'en')
        atomic(self.root / '.env', 'LAB_LANG=it\n')
        italian = menu_items(Lab(self.root, 'windows-11'))
        atomic(self.root / '.env', 'LAB_LANG=en\n')
        english = menu_items(Lab(self.root, 'windows-11'))
        self.assertIn('prerequisiti', italian[0]['label'])
        self.assertIn('manca la ISO', italian[0 + [i['command'][0] for i in italian].index('prepare')]['reason'])
        # Same commands in both languages: only the chrome changes.
        self.assertEqual([i['command'] for i in italian], [i['command'] for i in english])
        atomic(self.root / '.env', 'LAB_LANG=de\n')
        with self.assertRaisesRegex(LabError, 'LAB_LANG'):
            Lab(self.root)

    def test_ppm_preserves_whitespace_valued_pixels(self):
        data = b'P6\n# sample\n2 1\n255\n' + bytes([10, 32, 13, 0, 0, 0])
        width, height, pixels = ppm(data)
        self.assertEqual(pixels, bytes([10, 32, 13, 0, 0, 0]))
        image = png(width, height, pixels)
        self.assertTrue(image.startswith(b'\x89PNG'))
        offset, raster = 8, b''
        while offset < len(image):
            length = struct.unpack('!I', image[offset:offset+4])[0]
            kind = image[offset+4:offset+8]
            if kind == b'IDAT':
                raster += image[offset+8:offset+8+length]
            offset += length + 12
        self.assertEqual(zlib.decompress(raster), b'\0' + pixels)

    def test_black_frame_uses_serial_and_deduplicates_without_keys(self):
        self.lab.ensure()
        self.lab.serial.write_text('installer is working\n')
        def fake_qmp(lab, command, arguments=None):
            self.assertEqual(command, 'screendump')
            Path(arguments['filename']).write_bytes(b'P6\n1 1\n255\n\0\0\0')
        with patch('playground.screens.qmp', side_effect=fake_qmp):
            first = shot(self.lab, dedupe=True)
            second = shot(self.lab, dedupe=True)
        self.assertEqual(first, second)
        self.assertEqual(first.suffix, '.txt')
        self.assertIn('installer is working', first.read_text())
        self.assertEqual(len(records(first.parent / 'timeline.jsonl')), 1)

    def test_nudge_is_explicit_and_recorded(self):
        self.lab.ensure()
        calls = []
        def fake(lab, command, args):
            calls.append(command)
            if command == 'screendump':
                Path(args['filename']).write_bytes(b'P6\n1 1\n255\n\xff\xff\xff')
        with patch('playground.screens.qmp', side_effect=fake):
            shot(self.lab, nudge=True)
        self.assertEqual(calls, ['send-key', 'screendump'])
        self.assertEqual(records(self.lab.work / 'events.jsonl')[0]['kind'], 'intervention')
        self.assertIn('ASSISTED', records(self.lab.work / 'screenshots/timeline.jsonl')[0]['caption'])

    def test_report_escapes_embeds_and_preserves_attempt_history(self):
        self.lab.ensure()
        self.lab.cfg['LAB_PASSWORD'] = 'fixture-secret'
        self.lab.event(kind='installation', outcome='failed: first reason', duration=1)
        self.lab.event(kind='installation', outcome='passed', duration=2)
        self.lab.event(kind='command', command='<script>fixture-secret</script>')
        self.lab.serial.write_text('fixture-secret')
        target = report(self.lab)
        text = target.read_text()
        self.assertIn('attempt 2', text)
        self.assertIn('first reason', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('fixture-secret', text)
        self.assertIn('Latest screen', text)          # English by default
        self.assertIn('lang="en"', text)
        atomic(self.root / '.env', 'LAB_LANG=it\n')
        italian = report(Lab(self.root)).read_text()
        self.assertIn('Ultima schermata', italian)
        self.assertIn('Guida rapida', italian)            # the Italian guide stays either way
        self.assertIn('Guida rapida', text)

    def test_report_cleans_terminal_controls_without_changing_evidence(self):
        self.lab.ensure()
        self.lab.cfg['LAB_PASSWORD'] = 'fixture-secret'
        raw = ('[\x1b[0;32m  OK  \x1b[0m] Started \x1b[1me2scrub_all.timer\x1b[0m\r\n'
               '\x1b]0;terminal title\x07'
               '\x1b]8;;https://example.org\x1b\\link\x1b]8;;\x1b\\\n'
               '\x9b32mC1 color\x9b0m\n'
               'typoX\b\t<script>fixture-\x1b[31msecret</script>\x00\x07\n'
               'progress 10%\rprogress 100%\n')
        self.lab.serial.write_bytes(raw.encode())
        frame = self.lab.work / 'screenshots' / 'serial.txt'
        frame.parent.mkdir(exist_ok=True)
        frame.write_bytes(raw.encode())
        atomic(frame.parent / 'timeline.jsonl', json.dumps(dict(time=1, file=frame.name, caption='Serial fallback')) + '\n')
        self.lab.event(kind='command', command='\x1b[32mcheck\x1b[0m', outcome='\x1b[32mOK\x1b[0m')
        doc = report(self.lab).read_text()
        self.assertIn('[  OK  ] Started e2scrub_all.timer\n', doc)
        self.assertIn('link\nC1 color\ntypo\t&lt;script&gt;', doc)
        self.assertIn('progress 10%\nprogress 100%', doc)
        self.assertNotRegex(doc, r'[\x00-\x08\x0b-\x1f\x7f-\x9f]')
        self.assertNotIn('terminal title', doc)
        self.assertNotIn('fixture-secret', doc)
        self.assertIn('&lt;script&gt;', doc)
        self.assertEqual(self.lab.serial.read_bytes(), raw.encode())
        self.assertEqual(frame.read_bytes(), raw.encode())

    def test_agent_ping_reports_success_only_after_a_reply(self):
        with patch('playground.cli.agent', return_value={}):
            code, output = self.invoke('agent', self.lab.vm, 'ping')
        self.assertEqual(code, 0, output)
        self.assertIn('Guest agent OK', output)
        self.assertNotIn('{}', output)
        with patch('playground.cli.agent', side_effect=LabError('Agent timeout')):
            code, output = self.invoke('agent', self.lab.vm, 'ping')
        self.assertNotEqual(code, 0)
        self.assertIn('Agent timeout', output)
        self.assertNotIn('Guest agent OK', output)
        with patch('playground.cli.agent', return_value={'version': 'test'}):
            code, output = self.invoke('agent', self.lab.vm, 'info')
        self.assertEqual(json.loads(output), {'version': 'test'})

    def test_windows_redownload_never_deletes_before_reporting_missing_url(self):
        lab = Lab(self.root, 'windows-11')
        lab.iso.parent.mkdir()
        lab.iso.write_bytes(b'keep original ISO')
        code, output = self.invoke('iso', 'windows-11', 'redownload', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('No stable Windows download URL', output)
        self.assertEqual(lab.iso.read_bytes(), b'keep original ISO')

    def test_shared_key_cleanup_takes_both_lifecycle_locks(self):
        self.lab.safe('keys').mkdir()
        self.lab.safe('keys', 'id_ed25519').write_text('TEST ONLY')
        other = Lab(self.root, 'windows-11')
        with lock(other.oplock):
            code, output = self.invoke('clean', self.lab.vm, 'keys', '--yes')
        self.assertEqual(code, 1)
        self.assertTrue(self.lab.safe('keys', 'id_ed25519').exists())
        self.assertEqual(self.invoke('clean', self.lab.vm, 'keys', '--yes')[0], 0)
        self.assertFalse(self.lab.safe('keys', 'id_ed25519').exists())

    def test_install_timeout_captures_screen_and_keeps_vm(self):
        self._prepared_install()
        self.lab.cfg['LAB_INSTALL_TIMEOUT'] = '10'
        from playground.operations import configuration_digest
        prep = json.loads((self.lab.work / 'prepared.json').read_text())
        prep['config'] = configuration_digest(self.lab)
        atomic(self.lab.work / 'prepared.json', json.dumps(prep))
        self.lab.serial.write_text('still installing')
        with patch.object(Lab, 'pid', side_effect=[None, 999, 999, 999]), \
             patch.object(Lab, 'iso_state', return_value='verified'), \
             patch('playground.operations.start'), patch('playground.operations.shot') as capture, \
             patch('playground.operations.qmp', return_value={'clients': []}), \
             patch('playground.operations.time.monotonic', side_effect=[0, 11, 12]):
            with self.assertRaisesRegex(LabError, 'completion token'):
                install(self.lab)
        capture.assert_called_once()
        self.assertIn('TIMEOUT', capture.call_args.kwargs['caption'])
        self.assertTrue(self.lab.disk.exists())
        self.assertTrue((self.root / 'out/lubuntu-26.04.html').exists())

    def _prepared_install(self):
        from playground.operations import configuration_digest
        self.lab.ensure()
        self.lab.seed.write_bytes(b'seed')
        self.lab.disk.write_bytes(b'failed disk evidence')
        atomic(self.lab.work / 'prepared.json', json.dumps({'token': 'test', 'config': configuration_digest(self.lab), 'seed_sha256': sha256(self.lab.seed)}))

    def test_install_waits_for_exit_after_token(self):
        self._prepared_install()
        self.lab.serial.write_text('LAB_OK_test\n')
        # Checks: before start; loop running; loop exited; finally exited.
        with patch.object(Lab, 'pid', side_effect=[None, 999, None, None]), \
             patch.object(Lab, 'iso_state', return_value='verified'), \
             patch('playground.operations.start'), patch('playground.operations.shot'), \
             patch('playground.operations.qmp', return_value={'clients': []}), \
             patch('playground.operations.time.sleep') as sleep, patch('playground.operations.tpm_stop'):
            install(self.lab)
        sleep.assert_called_once_with(1)
        self.assertIn('passed', records(self.lab.work / 'events.jsonl')[-1]['outcome'])
        self.assertTrue(self.lab.disk.exists())

    def test_failure_token_captures_evidence_and_keeps_disk(self):
        self._prepared_install()
        self.lab.serial.write_text('LAB_FAIL_test\n')
        with patch.object(Lab, 'pid', side_effect=[None, 999, 999]), \
             patch.object(Lab, 'iso_state', return_value='verified'), \
             patch('playground.operations.start'), patch('playground.operations.shot') as capture:
            with self.assertRaisesRegex(LabError, 'failure token'):
                install(self.lab)
        capture.assert_called_once()
        self.assertTrue(self.lab.disk.exists())
        self.assertTrue((self.root / 'out/lubuntu-26.04.html').exists())


class SocketTests(unittest.TestCase):
    def serve(self, callback):
        temp = tempfile.TemporaryDirectory(prefix='qpl-socket-')
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'test.sock'
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(path))
        sock.listen(1)
        errors = []
        def handle():
            try:
                conn, _ = sock.accept()
                with conn:
                    conn.settimeout(2)
                    callback(conn)
            except BaseException as e:
                errors.append(e)
            finally:
                sock.close()
        thread = threading.Thread(target=handle, daemon=True)
        thread.start()
        return path, thread, errors

    def test_events_and_fragmented_qmp_reply(self):
        def server(conn):
            request = json.loads(conn.makefile('rb').readline())
            conn.sendall(b'{"event":"RESET"}\n')
            result = (json.dumps({'return': {'status': 'running'}, 'id': request['id']}) + '\n').encode()
            conn.sendall(result[:9])
            conn.sendall(result[9:])
        path, thread, errors = self.serve(server)
        with JsonSocket(path, timeout=1) as client:
            self.assertEqual(client.call('query-status'), {'status': 'running'})
        thread.join(2)
        self.assertFalse(errors)

    def test_silent_socket_has_bounded_contextual_error(self):
        def server(conn):
            time.sleep(.2)
        path, thread, _ = self.serve(server)
        with JsonSocket(path, timeout=.05) as client:
            with self.assertRaisesRegex(LabError, 'test.sock'):
                client.call('query-status')
        thread.join(2)


if __name__ == '__main__':
    unittest.main()
