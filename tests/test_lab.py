"""Isolated contract tests: never read the developer's .env, keys or VM files."""
import contextlib
import io
import json
import os
from pathlib import Path
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
from playground.core import DEFAULTS, Lab, LabError, atomic, lock, records, sha256
from playground.interface import menu_items
from playground.operations import clean, install, prepare, qemu_command, ssh, ssh_command
from playground.protocol import JsonSocket, agent, qmp
from playground.report import report
from playground.screens import png, ppm, shot
from playground.seeds import ubuntu_seed, windows_seed

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
        text = ubuntu_seed(cfg, 'ssh-ed25519 TEST_ONLY', '$6$TEST_ONLY', 'fixture-token')
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

    def test_ssh_isolated_and_windows_has_no_posix_shell(self):
        for vm in ('ubuntu-26.04', 'windows-11'):
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
        self.assertIn('prerequisiti', items[0]['label'])
        install_item = next(i for i in items if i['command'][0] == 'install')
        self.assertFalse(install_item['enabled'])
        self.assertIn('missing ISO', install_item['state'])
        self.assertTrue(any(i['command'][0] == 'start' for i in items))

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
        self.assertIn('Ultima schermata', text)

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
             patch('playground.operations.time.monotonic', side_effect=[0, 11, 12]):
            with self.assertRaisesRegex(LabError, 'completion token'):
                install(self.lab)
        capture.assert_called_once()
        self.assertIn('TIMEOUT', capture.call_args.kwargs['caption'])
        self.assertTrue(self.lab.disk.exists())
        self.assertTrue((self.root / 'out/ubuntu-26.04.html').exists())

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
        self.assertTrue((self.root / 'out/ubuntu-26.04.html').exists())


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
