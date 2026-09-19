"""Live viewer contracts using an isolated lab and a real loopback HTTP server."""
import contextlib
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from playground.cli import main, parser
from playground.core import Lab, LabError, lock
from playground.follow import follow
from playground.interface import menu_items

SOURCE = Path(__file__).resolve().parents[1]


class FollowCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qpl-follow-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / 'profiles', self.root / 'profiles')
        self.lab = Lab(self.root, 'windows-11')

    def test_dry_run_and_validation(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--root', str(self.root), 'shot', self.lab.vm,
                                   '--follow', '--dry-run']), 0)
        self.assertIn('every 2s', output.getvalue())
        self.assertFalse((self.root / 'work').exists())
        for interval in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(LabError):
                follow(self.lab, interval=interval, dry=True)
        with self.assertRaisesRegex(LabError, 'VM is stopped'):
            follow(self.lab)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['shot', self.lab.vm, '--follow', '--nudge'])

    def test_menu_follow_is_available_during_installation(self):
        self.lab.ensure()
        def entry():
            return next(i for i in menu_items(self.lab) if '--follow' in i['command'])
        self.assertFalse(entry()['enabled'])
        with lock(self.lab.oplock), patch.object(Lab, 'pid', return_value=123), \
                patch.object(Lab, 'ssh_banner', return_value=False):
            item = entry()
        self.assertTrue(item['enabled'])
        self.assertFalse(item['background'])
        self.assertEqual(item['command'], ['shot', 'windows-11', '--follow'])

    def test_live_frames_retry_stop_and_cleanup_without_guest_input(self):
        self.lab.ensure()
        self.lab.cfg['LAB_PASSWORD'] = 'fixture-secret'
        self.lab.serial.write_text('<installer> fixture-secret\n')
        urls, servers, failures = queue.Queue(), [], []
        pixels = [b'\xff\xff\xff']

        def fake_qmp(lab, command, arguments):
            self.assertEqual(command, 'screendump')
            raw = Path(arguments['filename'])
            raw.write_bytes(b'P6\n1 1\n255\n' + pixels[0])
            if pixels[0] == b'error':
                raise LabError('temporary fixture-secret failure')

        def make_server(*args):
            server = ThreadingHTTPServer(*args)
            servers.append(server)
            return server

        def run_viewer():
            try:
                follow(self.lab)
            except BaseException as error:
                failures.append(error)
                urls.put(None)

        with patch.object(Lab, 'pid', return_value=123) as pid, \
                patch('playground.screens.qmp', side_effect=fake_qmp) as qmp, \
                patch('playground.follow.ThreadingHTTPServer', side_effect=make_server), \
                patch('playground.follow.open_file', side_effect=urls.put) as opener, \
                contextlib.redirect_stdout(io.StringIO()):
            worker = threading.Thread(target=run_viewer, daemon=True)
            worker.start()
            try:
                url = urls.get(timeout=5)
                self.assertIsNotNone(url, failures)
                with urlopen(url, timeout=5) as response:
                    page = response.read().decode()
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn('interval = 2000', page)

                def frame():
                    with urlopen(url + 'frame', timeout=5) as response:
                        return json.load(response)

                self.assertTrue(frame()['image'].startswith('data:image/png;base64,'))
                pixels[0] = b'error'
                self.assertNotIn('fixture-secret', frame()['error'])
                self.assertFalse(list(self.lab.work.glob('follow-*/*.ppm')))
                pixels[0] = b'\0\0\0'
                text = frame()['text']
                self.assertIn('<installer>', text)
                self.assertNotIn('fixture-secret', text)
                pid.return_value = None
                self.assertEqual(frame(), {'stopped': True})
                self.assertEqual(qmp.call_count, 3)
                self.assertFalse((self.lab.work / 'screenshots').exists())
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + '../serial.log', timeout=5)
                self.assertEqual(error.exception.code, 404)
                opener.assert_called_once()
            finally:
                if servers:
                    servers[0].shutdown()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertFalse(list(self.lab.work.glob('follow-*')))
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_ctrl_c_closes_viewer_without_stopping_vm(self):
        self.lab.ensure()
        with patch.object(Lab, 'pid', return_value=123), \
                patch('playground.follow.ThreadingHTTPServer') as server, \
                patch('playground.follow.open_file'), \
                patch('playground.screens.qmp') as qmp, \
                contextlib.redirect_stdout(io.StringIO()):
            server.return_value.__enter__.return_value.serve_forever.side_effect = KeyboardInterrupt
            follow(self.lab)
        qmp.assert_not_called()
        server.return_value.__exit__.assert_called_once()
        self.assertFalse(list(self.lab.work.glob('follow-*')))
