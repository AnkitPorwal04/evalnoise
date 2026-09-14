"""Loopback-only coordinator transport and opt-in, independently allowlisted worker."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import threading
from urllib.parse import urlsplit

from .config import parse
from .coordinator import ControlError, MAX_BUNDLE, reviewed
from .docker import Docker
from .investigation import canonical, digest
from .runner import execute


def decode(data):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out: raise ValueError('Duplicate JSON key')
            out[key] = value
        return out
    def invalid(value):
        raise ValueError('Non-finite JSON value')
    result = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(result, dict): raise ValueError('Expected JSON object')
    return result


def server(control, port=4179):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, result):
            data = canonical(result)
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)

        def dispatch(self):
            try:
                host = self.headers.get('Host')
                assert isinstance(self.server, HTTPServer)
                port = self.server.server_port
                if len(self.headers.get_all('Host',[]))!=1 or len(self.headers.get_all('Authorization',[]))!=1 or host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                    raise ControlError('Host refused',403)
                if self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') not in (None,'none'):
                    raise ControlError('Browser-origin requests are not accepted',403)
                auth = self.headers.get('Authorization','')
                if not auth.startswith('Bearer '): raise ControlError('Authentication required',401)
                token = auth[7:]
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))>1:
                    raise ControlError('Ambiguous request framing',400)
                body = {}
                if self.command == 'POST':
                    length = int(self.headers.get('Content-Length','-1'))
                    if not 0 < length <= MAX_BUNDLE+65536: raise ControlError('Invalid request size',413)
                    if self.headers.get('Content-Type') != 'application/json': raise ControlError('JSON required',415)
                    data = self.rfile.read(length)
                    if len(data)!=length: raise ControlError('Incomplete body',400)
                    body = decode(data)
                routes = {
                    ('POST','/submit'): (control.submit, {'catalog','request_key'}),
                    ('POST','/claim'): (control.claim, set()),
                    ('POST','/heartbeat'): (control.heartbeat, {'job','lease'}),
                    ('POST','/complete'): (control.complete, {'job','lease','bundle'}),
                    ('POST','/fail'): (control.fail, {'job','lease'}),
                    ('POST','/cancel'): (lambda t,job: control.owner_action(t,job,'cancel'), {'job'}),
                    ('POST','/purge'): (lambda t,job: control.owner_action(t,job,'purge'), {'job'}),
                    ('POST','/revoke'): (control.revoke, {'principal'}),
                    ('GET','/jobs'): (control.owner_jobs, set()),
                    ('GET','/audit'): (control.audit, set()),
                }
                if self.command=='GET' and self.path.startswith('/artifact/'):
                    result = control.owner_jobs(token,self.path[10:],artifact=True)
                else:
                    route = routes.get((self.command,self.path))
                    if route is None: raise ControlError('Route not found',404)
                    function, fields = route
                    if set(body)!=fields: raise ControlError('Unexpected request fields',400)
                    if any(not isinstance(value,str) or len(value)>256 for key,value in body.items() if key!='bundle'):
                        raise ControlError('Invalid request field type or length',400)
                    result = function(token,**body)
                self.reply(200,result)
            except ControlError as error:
                self.reply(error.status,{'error':str(error)})
            except (ValueError,TypeError,KeyError,RecursionError) as error:
                self.reply(400,{'error':'Malformed request or evidence'})
            except sqlite3.Error:
                self.reply(503,{'error':'Coordinator storage unavailable'})
            except (BrokenPipeError,ConnectionResetError,TimeoutError):
                self.close_connection=True

        do_GET = dispatch
        do_POST = dispatch

    class BoundedServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection,address
    return BoundedServer(('127.0.0.1',port),Handler)


def credential(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077 or path.stat().st_size>16384:
        raise ControlError('Credentials must be a private, bounded regular file')
    return decode(path.read_bytes())


class Client:
    def __init__(self, url, token):
        address = urlsplit(url)
        if address.scheme!='http' or address.hostname!='127.0.0.1' or address.username or address.password or address.path not in ('','/') or address.query or address.fragment:
            raise ControlError('Only explicit http://127.0.0.1:PORT is supported')
        self.port = address.port or 4179
        self.token = token

    def request(self, path, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1',self.port,timeout=15)
        try:
            data = canonical(payload) if payload is not None else None
            connection.request('POST' if data else 'GET', path, data,
                               {'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            response=connection.getresponse(); raw=response.read(MAX_BUNDLE+65537)
            if len(raw)>MAX_BUNDLE+65536: raise ControlError('Response exceeds bound')
            result=decode(raw)
            if response.status!=200: raise ControlError(result.get('error','Request refused'),response.status)
            return result
        finally:
            connection.close()


def worker_once(client, allowed_images, output, *, backend=None, executor=execute):
    """One lease, no silent retries. Heartbeat loss requests cooperative cancellation."""
    lease = client.request('/claim',{})
    if lease['job'] is None: return {'state':'idle'}
    stop=threading.Event(); done=threading.Event(); heartbeat_errors=[]
    identity={'job':lease['job'],'lease':lease['lease']}
    def beat():
        while not done.wait(max(.1,lease['lease_seconds']/3)):
            try: client.request('/heartbeat',identity)
            except (ControlError,OSError,ValueError,http.client.HTTPException) as error:
                heartbeat_errors.append(type(error).__name__)
                stop.set(); return
    thread=threading.Thread(target=beat,name='evalnoise-worker-heartbeat',daemon=True)
    previous={}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig]=signal.signal(sig,lambda *_:stop.set())
    try:
        if digest(lease['config'])!=lease['config_sha256']: raise ControlError('Catalog digest mismatch')
        experiment, images=reviewed(lease['config'])
        if not set(images).issubset(set(allowed_images)): raise ControlError('Worker image allowlist refused catalog')
        thread.start()
        engine=backend or Docker()
        directory=executor(experiment,Path(output),backend=engine,stop=stop)
        if (directory/'manifest.json').stat().st_size>MAX_BUNDLE: raise ControlError('Worker manifest exceeds upload bound')
        manifest=decode((directory/'manifest.json').read_bytes())
        trials=[]; total=0
        for path in sorted((directory/'trials').glob('*.json')):
            if path.stat().st_size>MAX_BUNDLE: raise ControlError('Worker artifact exceeds bound')
            total+=path.stat().st_size
            if total>MAX_BUNDLE: raise ControlError('Worker artifacts exceed upload bound')
            trials.append(decode(path.read_bytes()))
        if stop.is_set() or heartbeat_errors: raise ControlError('Lease or process cancellation; evidence retained locally')
        result=client.request('/complete',{**identity,'bundle':{'manifest':manifest,'trials':trials}})
        return {'state':'completed','job':lease['job'],'directory':str(directory),'receipt':result}
    except Exception:
        stop.set()
        try: client.request('/fail',identity)
        except (ControlError,OSError,ValueError,http.client.HTTPException):
            # A fenced or disconnected worker cannot alter coordinator state.
            heartbeat_errors.append('failure_report_refused')
        raise
    finally:
        done.set()
        if thread.ident is not None: thread.join(16)
        for sig,handler in previous.items(): signal.signal(sig,handler)
