from __future__ import annotations
import base64, json, threading, uuid
from .session import TerminalSession
class TerminalManager:
    def __init__(self): self.sessions={}; self._lock=threading.RLock()
    def create(self, session_id=None, **kwargs):
        sid=session_id or uuid.uuid4().hex
        with self._lock: self.sessions[sid]=TerminalSession(**kwargs)
        return sid,self.sessions[sid]
    def get(self,sid): return self.sessions[sid]
    def close(self,sid):
        s=self.sessions.pop(sid,None)
        if s:s.close()
manager=TerminalManager()
def websocket_bridge(ws, session:TerminalSession):
    lock=threading.Lock()
    def output(data):
        with lock: ws.send(json.dumps({'type':'output','data':base64.b64encode(data).decode()}))
    unsub=session.subscribe(output,replay=True)
    try:
        while True:
            raw=ws.receive()
            if raw is None: break
            msg=json.loads(raw)
            if msg.get('type')=='input': session.write(base64.b64decode(msg['data']))
            elif msg.get('type')=='resize': session.resize(int(msg['cols']),int(msg['rows']))
            elif msg.get('type')=='run': session.run(str(msg['command']))
    finally: unsub()
def flask_blueprint(manager_instance=manager, url_prefix='/terminal'):
    try:
        from flask import Blueprint, jsonify, request
        from flask_sock import Sock
    except ImportError as e: raise RuntimeError('Install notebook-terminal[flask]') from e
    bp=Blueprint('notebook_terminal',__name__,url_prefix=url_prefix); sock=Sock(bp)
    @bp.post('/sessions')
    def create_session():
        body=request.get_json(silent=True) or {}; sid,_=manager_instance.create(**body); return jsonify({'id':sid})
    @bp.delete('/sessions/<sid>')
    def delete_session(sid): manager_instance.close(sid); return ('',204)
    @sock.route('/ws/<sid>')
    def ws_route(ws,sid): websocket_bridge(ws,manager_instance.get(sid))
    return bp

def django_consumer(manager_instance=manager):
    try:
        from channels.generic.websocket import WebsocketConsumer
    except ImportError as e: raise RuntimeError('Install notebook-terminal[django]') from e
    class TerminalConsumer(WebsocketConsumer):
        def connect(self):
            self.sid=self.scope['url_route']['kwargs']['sid']; self.session=manager_instance.get(self.sid); self.accept()
            self.unsubscribe=self.session.subscribe(lambda d:self.send(text_data=json.dumps({'type':'output','data':base64.b64encode(d).decode()})),replay=True)
        def receive(self,text_data=None,bytes_data=None):
            m=json.loads(text_data or '{}')
            if m.get('type')=='input': self.session.write(base64.b64decode(m['data']))
            elif m.get('type')=='resize': self.session.resize(int(m['cols']),int(m['rows']))
            elif m.get('type')=='run': self.session.run(str(m['command']))
        def disconnect(self,code): self.unsubscribe()
    return TerminalConsumer
