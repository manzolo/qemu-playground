"""Bounded JSON socket protocols, with a single lock per QMP/QGA endpoint."""
import json
import secrets
import socket
import time
from .core import LabError, lock


class JsonSocket:
    def __init__(self, path, timeout=10):
        self.path, self.timeout = path, timeout
        self.buffer = b''

    def __enter__(self):
        self.guard = lock(self.path.with_suffix('.lock'), self.timeout)
        self.guard.__enter__()
        self.deadline = time.monotonic() + self.timeout
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(str(self.path))
        except BaseException:
            self.sock.close()
            self.guard.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        self.sock.close()
        self.guard.__exit__(*args)

    def send(self, value, prefix=b''):
        self.sock.sendall(prefix + json.dumps(value).encode() + b'\n')

    def receive(self):
        while True:
            if time.monotonic() >= self.deadline:
                raise LabError(f'Timeout waiting for protocol response from {self.path}')
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                try:
                    return json.loads(line.lstrip(b'\xff\r'))
                except ValueError:
                    continue  # stale partial data before guest-sync-delimited
            self.sock.settimeout(max(.01, self.deadline - time.monotonic()))
            try:
                data = self.sock.recv(65536)
            except TimeoutError as e:
                raise LabError(f'Timeout waiting for protocol response from {self.path}') from e
            if not data:
                raise LabError(f'Connection closed while waiting for {self.path}')
            self.buffer += data
            if len(self.buffer) > 8 * 1024 * 1024:
                raise LabError('Protocol response exceeds 8 MiB')

    def call(self, command, arguments=None, no_reply=False):
        ident = secrets.token_hex(8)
        msg = dict(execute=command, id=ident)
        if arguments is not None:
            msg['arguments'] = arguments
        self.send(msg)
        if no_reply:
            return None
        while True:
            value = self.receive()
            if value.get('id') != ident:
                continue
            if 'error' in value:
                raise LabError(f'{command}: {value["error"]}')
            if 'return' in value:
                return value['return']


def qmp(lab, command, arguments=None):
    if not lab.pid():
        raise LabError('QMP refused: VM process is not running or not owned by this lab')
    with JsonSocket(lab.qmp) as client:
        if 'QMP' not in client.receive():
            raise LabError('Missing QMP greeting')
        client.call('qmp_capabilities')
        return client.call(command, arguments)


def agent(lab, command):
    choices = {'ping': 'guest-ping', 'info': 'guest-info', 'osinfo': 'guest-get-osinfo',
               'ip': 'guest-network-get-interfaces', 'shutdown': 'guest-shutdown'}
    if command not in choices:
        raise LabError('Agent command must be: ' + ', '.join(choices))
    if not lab.pid():
        raise LabError('QGA refused: VM process is not running or not owned by this lab')
    with JsonSocket(lab.qga) as client:
        sync = secrets.randbits(48)
        client.send({'execute': 'guest-sync-delimited', 'arguments': {'id': sync}}, prefix=b'\xff')
        while client.receive().get('return') != sync:
            pass
        return client.call(choices[command], {'mode': 'powerdown'} if command == 'shutdown' else None,
                           no_reply=command == 'shutdown')
