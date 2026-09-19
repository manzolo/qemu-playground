"""Opt-in real QEMU smoke tests. No ISO, operating system, KVM or personal files.
Run: python3 -m unittest discover -s tests -p smoke_qemu.py -v
"""
import concurrent.futures
import json
from pathlib import Path
import shutil
import socket
import tempfile
import time
import unittest
from playground.core import Lab, atomic, port_free, run, sha256
from playground.operations import prepare, start, tpm_stop
from playground.protocol import qmp
from playground.screens import shot
from playground.report import report

SOURCE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('qemu-system-x86_64') and shutil.which('qemu-img'), 'QEMU required')
class RealQemu(unittest.TestCase):
    def smoke(self, vm):
        with tempfile.TemporaryDirectory(prefix='qpl-real-') as name:
            root = Path(name)
            shutil.copytree(SOURCE / 'profiles', root / 'profiles')
            with socket.socket() as port:
                port.bind(('127.0.0.1', 0))
                number = port.getsockname()[1] - (vm == 'windows-11')
            # VNC has to live in 5900-5998, so it cannot borrow an ephemeral port like
            # SSH does: find a free pair, or run without a console rather than collide
            # with whatever else on this machine already holds one.
            vnc = next((p for p in range(5900, 5998) if port_free(p) and port_free(p + 1)), 0)
            atomic(root / '.env', f'LAB_ACCEL=tcg\nLAB_RAM_MB=1024\nLAB_CPUS=1\n'
                                  f'LAB_SSH_PORT={number}\nLAB_VNC_PORT={vnc}\n')
            lab = Lab(root, vm)
            lab.ensure()
            run(['qemu-img', 'create', '-f', 'qcow2', lab.disk, '64M'])
            if vm == 'windows-11':
                shutil.copyfile(lab.cfg['LAB_OVMF_VARS'], lab.work / 'OVMF_VARS.fd')
            try:
                start(lab)
                self.assertIsNotNone(lab.pid())
                if lab.vnc:
                    self.assertTrue(qmp(lab, 'query-vnc')['enabled'])
                    self.assertEqual(qmp(lab, 'query-vnc')['clients'], [])
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(lambda _: qmp(lab, 'query-status'), range(6)))
                self.assertTrue(all(r['status'] == 'running' for r in results))
                time.sleep(2)
                screenshot = shot(lab, caption='Real QEMU firmware smoke test; no OS installation')
                self.assertIn(screenshot.suffix, ('.png', '.txt'))
                html = report(lab).read_text()
                self.assertIn('Not yet validated', html)
                if screenshot.suffix == '.png':
                    self.assertIn('data:image/png;base64,', html)
                qmp(lab, 'quit')
                deadline = time.monotonic() + 5
                while lab.pid() and time.monotonic() < deadline:
                    time.sleep(.1)
                self.assertIsNone(lab.pid())
            finally:
                if lab.pid():
                    qmp(lab, 'quit')
                    time.sleep(.3)
                tpm_stop(lab)

    @unittest.skipUnless(all(shutil.which(c) for c in ('xorriso', 'genisoimage', 'openssl', 'ssh-keygen')), 'seed tools required')
    def test_real_lubuntu_preparation_with_synthetic_iso(self):
        with tempfile.TemporaryDirectory(prefix='qpl-seed-') as name:
            root = Path(name)
            shutil.copytree(SOURCE / 'profiles', root / 'profiles')
            atomic(root / '.env', 'LAB_PASSWORD=TEST_ONLY_not_a_real_password\n')
            lab = Lab(root)
            tree = root / 'fixture-media' / 'casper'
            tree.mkdir(parents=True)
            (tree / 'vmlinuz').write_bytes(b'Synthetic test kernel, not bootable')
            (tree / 'initrd').write_bytes(b'Synthetic test initrd, not bootable')
            lab.iso.parent.mkdir()
            run(['xorriso', '-as', 'mkisofs', '-quiet', '-o', lab.iso, tree.parent], capture=True)
            # Only this temporary fixture instance trusts the generated fixture.
            # Production profiles and vendor pins are never changed.
            lab.profile['sha256'] = sha256(lab.iso)
            self.assertEqual(lab.iso_state(full=True), 'verified')
            # A host key pinned by an earlier install of this profile must not survive
            # a new disk, or every reinstall greets the user with a MITM banner.
            (root / 'keys').mkdir(exist_ok=True)
            stale = root / 'keys' / 'known_hosts'
            stale.write_text(f'[127.0.0.1]:{lab.port} ssh-ed25519 AAAATESTONLYSTALEKEY\n')
            prepare(lab)
            self.assertNotIn(f'[127.0.0.1]:{lab.port}', stale.read_text())
            self.assertTrue(lab.disk.stat().st_size > 0)
            self.assertTrue(lab.seed.stat().st_size > 0)
            self.assertEqual((lab.work / 'vmlinuz').read_bytes(), (tree / 'vmlinuz').read_bytes())
            data = root / 'extracted-user-data'
            run(['xorriso', '-osirrox', 'on', '-indev', lab.seed, '-extract', '/user-data', data], capture=True)
            seed = json.loads(data.read_text().split('\n', 1)[1])['autoinstall']
            self.assertFalse(seed['ssh']['allow-pw'])
            self.assertIn('lubuntu-desktop', seed['packages'])
            self.assertEqual(seed['ssh']['authorized-keys'][0], (root / 'keys/id_ed25519.pub').read_text().strip())
            self.assertEqual((root / 'keys/id_ed25519').stat().st_mode & 0o777, 0o600)

    def test_linux_firmware_qmp_concurrency_and_screenshot(self):
        self.smoke('lubuntu-26.04')

    @unittest.skipUnless(shutil.which('swtpm') and Path('/usr/share/OVMF/OVMF_VARS_4M.ms.fd').exists(), 'swtpm + OVMF required')
    def test_windows_firmware_tpm_qmp_and_screenshot(self):
        self.smoke('windows-11')
