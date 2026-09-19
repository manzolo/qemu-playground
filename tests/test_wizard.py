"""Wizard progression uses isolated files and never launches a VM."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

from playground.cli import main
from playground.core import Lab, atomic, lock
from playground.interface import menu_items
from playground.operations import configuration_digest
from playground.wizard import PAGES, screen


class WizardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='qpl-wizard-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'profiles', self.root / 'profiles')
        atomic(self.root / '.env', 'LAB_PASSWORD=Test-password-123\n')
        self.lab = Lab(self.root)
        for target, value in (('playground.interface.readiness', {'ready': True}),
                              ('playground.interface.port_free', True)):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def primary(self):
        return screen(self.lab)[1][0]

    def prepare_files(self):
        self.lab.ensure()
        self.lab.disk.write_bytes(b'pretend disk')
        self.lab.seed.write_bytes(b'pretend seed')
        atomic(self.lab.work / 'prepared.json', json.dumps(
            {'config': configuration_digest(self.lab), 'token': 'test'}))

    def test_fresh_lab_then_verified_iso_then_prepared_disk(self):
        self.assertEqual(self.primary()['command'], ['iso', self.lab.vm, 'download'])
        with patch.object(Lab, 'iso_state', return_value='verified'):
            self.assertEqual(self.primary()['command'], ['prepare', self.lab.vm])
            self.prepare_files()
            self.assertEqual(self.primary()['command'], ['install', self.lab.vm])
            self.lab.cfg['LAB_CPUS'] = '8'
            self.assertEqual(self.primary()['command'], ['prepare', self.lab.vm])

    def test_prerequisites_and_password_come_first(self):
        with patch('playground.interface.readiness', return_value={'ready': False}):
            self.assertEqual(self.primary()['command'], ['doctor', '--install'])
        self.lab.cfg['LAB_PASSWORD'] = ''
        self.assertEqual(self.primary()['command'], ['config', 'init'])

    def test_existing_unverified_iso_is_verified_instead_of_downloaded(self):
        self.lab.ensure()
        self.lab.iso.parent.mkdir()
        self.lab.iso.write_bytes(b'unverified')
        self.assertEqual(self.primary()['command'], ['iso', self.lab.vm, 'verify'])
        self.assertIn('ISO not verified', screen(self.lab)[0])
        with patch.object(Lab, 'iso_state', return_value='SHA-256 MISMATCH: delete or redownload ISO'):
            self.assertEqual(self.primary()['command'], ['iso', self.lab.vm, 'redownload'])

    def test_background_work_recommends_refresh_even_if_vm_is_running(self):
        self.prepare_files()
        with lock(self.lab.oplock), patch.object(Lab, 'pid', return_value=123), \
                patch.object(Lab, 'ssh_banner', return_value=False):
            self.assertEqual(self.primary()['id'], 'nav:home')
            self.assertIn('operation is running', screen(self.lab)[0])

    def test_installation_evidence_belongs_to_current_attempt(self):
        self.prepare_files()
        atomic(self.lab.work / 'attempt.json', '{"start": 100}')
        events = self.lab.work / 'events.jsonl'
        atomic(events, '{"kind": "installation", "time": 50, "outcome": "passed (unattended)"}\n')
        # A verdict older than the attempt is not this attempt's verdict, so the
        # attempt still counts as unrecorded and recovery is what it needs.
        self.assertEqual(self.primary()['command'], ['recover', self.lab.vm])
        atomic(events, '{"kind": "installation", "time": 101, "outcome": "passed (unattended)"}\n')
        # Booting an installed disk does not require the original ISO or spare install RAM.
        with patch('playground.interface.readiness', return_value={'ready': False}):
            self.assertEqual(self.primary()['command'], ['start', self.lab.vm])
        with patch.object(Lab, 'pid', return_value=123), patch.object(Lab, 'ssh_banner', return_value=True):
            self.assertEqual(self.primary()['id'], 'nav:access')
        atomic(events, '{"kind": "installation", "time": 101, "outcome": "failed"}\n')
        self.assertEqual(self.primary()['command'], ['report', self.lab.vm, '--open'])

    def test_untracked_disk_is_not_recommended_for_overwrite(self):
        self.lab.ensure()
        self.lab.disk.write_bytes(b'existing data')
        with patch.object(Lab, 'iso_state', return_value='verified'):
            self.assertEqual(self.primary()['id'], 'nav:machine')

    def test_small_home_and_all_existing_actions_reachable(self):
        self.assertLessEqual(len(screen(self.lab)[1]), 6)
        self.assertFalse(any(i['command'][:1] == ['clean'] for i in screen(self.lab)[1]))
        expected = {i['id'] for i in menu_items(self.lab)}
        actual = {i['id'] for page in PAGES for i in screen(self.lab, page)[1]
                  if i['id'].isdecimal()}
        self.assertEqual(expected, actual)
        for page in PAGES[1:]:
            self.assertEqual(screen(self.lab, page)[1][-1]['label'], 'Back')

    def test_windows_missing_media_has_an_actionable_import_guide(self):
        self.lab = Lab(self.root, 'windows-11')
        self.assertEqual(self.primary()['id'], 'nav:media')
        self.assertIn('import --source /path/to/vendor.iso', screen(self.lab, 'media')[0])
        with patch.object(Lab, 'iso_state', return_value='verified'):
            self.assertEqual(self.primary()['id'], 'nav:setup')
            self.assertIn('LAB_QGA', screen(self.lab)[0])

    def test_navigation_preview_copy_and_dispatch(self):
        for action in ('_menu', '_preview', '_command', '_execute'):
            output = io.StringIO()
            args = ['--root', str(self.root), action, self.lab.vm, '--page', 'home']
            if action != '_menu':
                args += ['nav:tools']
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                code = main(args)
            self.assertEqual(code, 1 if action == '_execute' else 0)
            if action == '_command':
                self.assertEqual(output.getvalue(), '')
        self.assertFalse((self.root / 'work').exists())

    def test_italian_wizard_preserves_actions(self):
        english = self.primary()['command']
        self.lab.cfg['LAB_LANG'] = 'it'
        self.assertEqual(self.primary()['command'], english)
        self.assertIn('Passo 2/5', screen(self.lab)[0])
        self.assertEqual(screen(self.lab, 'tools')[1][-1]['label'], 'Indietro')

    @unittest.skipUnless(shutil.which('fzf'), 'fzf is needed for terminal navigation')
    def test_terminal_navigation_back_profile_and_quit(self):
        source = Path(__file__).resolve().parents[1]
        for folder in ('playground', 'scripts'):
            shutil.copytree(source / folder, self.root / folder)
        shutil.copy2(source / 'lab', self.root / 'lab')
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(TERM='xterm-256color', LAB_NO_TMUX='1')
            os.environ.pop('TMUX', None)
            os.execv(str(self.root / 'lab'), [str(self.root / 'lab')])
        import fcntl
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 36, 100, 0, 0))

        def wait_for(text):
            output = b''
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .1)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    output += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(fd, b'\x1b[1;1R')
                    if text.encode() in output:
                        return
            self.fail(f'Terminal did not show {text!r}: {output[-2000:]!r}')

        try:
            wait_for('Guided setup')
            os.write(fd, b'Settings')
            time.sleep(.15)
            os.write(fd, b'\r')
            wait_for('Esc back')
            os.write(fd, b'Cleanup')
            time.sleep(.15)
            os.write(fd, b'\r')
            wait_for('Delete the disk')
            os.write(fd, b'\x1b')
            wait_for('Prerequisites and configuration')
            os.write(fd, b'\x1b')
            wait_for('Guided setup')
            os.write(fd, b'\x10')  # Ctrl-P
            wait_for('Choose a profile')
            os.write(fd, b'Windows')
            time.sleep(.15)
            os.write(fd, b'\r')
            wait_for('windows-11')
            os.write(fd, b'\x1b')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                ended, status = os.waitpid(pid, os.WNOHANG)
                if ended:
                    pid = None
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    break
                time.sleep(.05)
            self.assertIsNone(pid, 'Esc at home should exit')
        finally:
            if pid:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            os.close(fd)


if __name__ == '__main__':
    unittest.main()
