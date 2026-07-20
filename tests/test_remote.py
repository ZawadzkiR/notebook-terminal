import json

import pytest

from notebook_terminal.remote import RemoteTerminalSession, _normalise_server_url, _websocket_url


def test_urls():
    assert _normalise_server_url(server_url=None, hub_url='https://hub.example', username='alice', server_name=None) == 'https://hub.example/user/alice/'
    assert _normalise_server_url(server_url='https://hub.example/user/a/', hub_url=None, username=None, server_name=None) == 'https://hub.example/user/a/'
    assert _websocket_url('https://hub.example/user/a/terminals/websocket/1') == 'wss://hub.example/user/a/terminals/websocket/1'


def test_remote_protocol(monkeypatch):
    sent = []

    class Response:
        status_code = 200
        text = ''
        def json(self):
            return {'name': '7'}

    class FakeHTTP:
        def __init__(self):
            self.headers = {}
            self.cookies = type('Cookies', (), {'get_dict': lambda self: {}})()
        def request(self, method, url, **kwargs):
            assert kwargs['verify'] is True
            return Response()
        def close(self):
            pass

    class FakeWS:
        def send(self, value):
            sent.append(json.loads(value))
        def recv(self):
            raise OSError('stop')
        def close(self):
            pass

    monkeypatch.setattr('notebook_terminal.remote.requests.Session', FakeHTTP)
    monkeypatch.setattr('notebook_terminal.remote.websocket.create_connection', lambda *a, **k: FakeWS())

    session = RemoteTerminalSession(token='secret', server_url='https://hub.example/user/alice/')
    session.run('ls -la')
    session.resize(120, 40)
    assert ['stdin', 'ls -la\n'] in sent
    assert ['set_size', 40, 120, 0, 0] in sent
    session.close()


def test_terminal_factory_requires_token():
    from notebook_terminal.notebook import terminal
    with pytest.raises(TypeError):
        terminal(backend='jupyterhub', server_url='https://hub.example/user/alice/', auto_display=False)
