"""Read-only loopback artifact viewer. No execution endpoints."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import html
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

SAFE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
LIMIT = 16 * 1024 * 1024
STYLE = """*{box-sizing:border-box}body{margin:0;background:#f4f2ea;color:#193c34;font:16px/1.5 Georgia,serif}header,main,footer{max-width:1300px;margin:auto;padding:25px 5%}header{border-bottom:1px solid #c9d2c7;display:flex;justify-content:space-between}a{color:inherit}header a{text-decoration:none;font-weight:bold}small,.meta{font:12px/1.6 monospace;color:#61736b}h1{font-size:clamp(32px,5vw,56px);line-height:1.1;letter-spacing:-1px;overflow-wrap:anywhere}h2{font-size:26px}p{max-width:850px}.scroll{overflow:auto;border:1px solid #c9d2c7;max-height:600px}table{border-collapse:collapse;width:100%;font:12px monospace}th,td{text-align:left;padding:14px;border-bottom:1px solid #c9d2c7;white-space:nowrap}th{background:#e6eae0;position:sticky;top:0}pre{background:#e9ece3;padding:20px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow:auto;font:12px/1.6 monospace}details{border-top:1px solid #c9d2c7;margin:22px 0;padding-top:15px}summary{cursor:pointer}input,select{padding:12px;background:#fffdf7;border:1px solid #c9d2c7;font:13px monospace;max-width:100%}.filters{display:flex;gap:14px;flex-wrap:wrap;margin:25px 0}label{display:grid;gap:5px;font:12px monospace}.stats{display:flex;gap:35px;flex-wrap:wrap;padding:20px 0;border-block:1px solid #c9d2c7}.stats b{display:block;font-size:32px}.stats span{font:11px monospace}footer{font:11px monospace}a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #c49338;outline-offset:3px}"""
SCRIPT = """const filters=[...document.querySelectorAll('[data-filter]')];function apply(){let count=0;for(const row of document.querySelectorAll('tbody tr')){const show=filters.every(f=>{const text=f.dataset.filter==='search'?row.textContent:row.dataset[f.dataset.filter];return !f.value || (f.dataset.filter==='search'?text.toLowerCase().includes(f.value.toLowerCase()):text===f.value)});row.hidden=!show;if(show)count++}document.getElementById('count').textContent=count+' visible records'}for(const f of filters)f.addEventListener('input',apply);if(filters.length)apply();"""


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} — EvalNoise</title><link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script></head><body><header><a href="/">evalnoise / workbench</a><small>LOCAL · READ ONLY</small></header><main><p class="meta">M5 / EVIDENCE EXPLORER</p><h1>{esc(title)}</h1>{body}</main><footer>Recorded local artifacts · No Docker controls · No model calls · Missing is not zero</footer></body></html>').encode()


def details(title, value):
    return f'<details><summary>{esc(title)}</summary><pre>{esc(json.dumps(value, indent=2, ensure_ascii=True))}</pre></details>'


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Artifact root must be a directory")

    def read(self, run, file, trial=False):
        if not SAFE.fullmatch(run) or not SAFE.fullmatch(file):
            raise ValueError("Invalid identifier")
        directory = self.root / run
        path = directory / 'trials' / file if trial else directory / file
        if directory.is_symlink() or path.is_symlink() or directory.resolve().parent != self.root:
            raise ValueError("Symlink or outside artifact")
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory_fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                if trial:
                    nested_fd = os.open('trials', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = nested_fd
                fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, 'rb') as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError('Artifact must be a regular file')
                    data = source.read(LIMIT + 1)
            finally:
                os.close(directory_fd)
        finally:
            os.close(root_fd)
        if len(data) > LIMIT:
            raise ValueError("Artifact exceeds viewing limit")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return value

    def library(self):
        rows = []
        paths = list(islice(self.root.iterdir(), 1001))
        if len(paths) > 1000:
            raise ValueError("Root exceeds 1000 entries")
        for path in sorted(paths):
            if path.is_symlink() or not path.is_dir() or not SAFE.fullmatch(path.name) or not (path / 'manifest.json').exists():
                continue
            try:
                m = self.read(path.name, 'manifest.json')
                name, status, kind = m['config']['name'], m['status'], m.get('measurement_kind', 'unknown')
            except (OSError, ValueError, KeyError, TypeError):
                name, status, kind = path.name, 'unreadable', 'unknown'
            rows.append(f'<tr><td><a href="/run/{esc(path.name)}">{esc(name)}</a></td><td>{esc(status)}</td><td>{esc(kind)}</td><td>{esc(path.name)}</td></tr>')
        return page('Look beyond the score.', '<p>Browse outcomes, resource profiles, agent traces, and verifier evidence. This workbench never executes experiments or regenerates historical files.</p><label>Find a run<input data-filter="search" placeholder="Name, status, or run ID"></label><p id="count" class="meta"></p><div class="scroll"><table><thead><tr><th>Run</th><th>Status</th><th>Contract</th><th>Directory</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')

    def run(self, name):
        m = self.read(name, 'manifest.json')
        records, total = [], 0
        for path in sorted((self.root / name / 'trials').glob('*.json')):
            total += path.stat().st_size
            if len(records) >= 10000 or total > 64 * 1024 * 1024:
                raise ValueError('Run exceeds viewing limits')
            records.append((path.name, self.read(name, path.name, trial=True)))
        passed = sum(t.get('status') == 'passed' for _, t in records)
        planned = sum(len(b['trials']) for b in m['plan'])
        stats = ''.join(f'<div><b>{v}</b><span>{k}</span></div>' for k,v in [('Recorded',len(records)),('Clean passes',passed),('Other recorded',len(records)-passed),('Missing',max(0,planned-len(records)))])
        filters = '<label>Search<input data-filter="search" placeholder="Trial or task"></label>'
        for key in ('profile','status','task'):
            values = sorted({str(t.get(key, 'unknown')) for _,t in records})
            options = ''.join(f'<option>{esc(v)}</option>' for v in values)
            filters += f'<label>{key.title()}<select data-filter="{key}"><option value="">All</option>{options}</select></label>'
        rows = []
        for file,t in records:
            attrs = ' '.join(f'data-{k}="{esc(t.get(k, "unknown"))}"' for k in ('profile','status','task'))
            cells = ''.join(f'<td>{esc(t.get(k, "Not recorded"))}</td>' for k in ('task','profile','repeat','status'))
            duration = t.get('container_duration_s')
            rows.append(f'<tr {attrs}>{cells}<td>{esc(duration if duration is not None else "Not measured")}</td><td><a href="/trial/{esc(name)}/{esc(file)}">Inspect evidence</a></td></tr>')
        return page(m['config']['name'], f'<p class="meta">{esc(name)} · {esc(m["status"])}</p><div class="stats">{stats}</div><p>Display of recorded evidence, not an integrity attestation. Active runs may change between reads. All recorded non-passes remain visible; missing trials are separate.</p><div class="filters">{filters}</div><p id="count" class="meta"></p><div class="scroll"><table><thead><tr><th>Task</th><th>Profile</th><th>Repeat</th><th>Outcome</th><th>Workload seconds</th><th>Evidence</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>' + details('Resource profiles',m['config']['profiles']) + details('Engine provenance',{'environment':m.get('environment'),'identity':m.get('engine_identity'),'final':m.get('engine_identity_final')}) + details('Full manifest',m))

    def trial(self, run, file):
        if not file.endswith('.json') or file == 'manifest.json':
            raise ValueError('Not a trial artifact')
        self.read(run, 'manifest.json')
        t = self.read(run, file, trial=True)
        body = f'<p><a href="/run/{esc(run)}">← Back to run</a></p><p class="meta">{esc(t.get("status"))} · {esc(t.get("task"))} · {esc(t.get("profile"))}</p>'
        for key in ('agent','verification','resource_audit','telemetry_meta','logs','error'):
            if t.get(key) is not None:
                body += details(key.replace('_',' ').title(), t[key])
        body += details('Complete trial record',t)
        return page(t.get('id',file),body)


def server(root, port=4178):
    store = Store(root)
    class LocalServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def respond(self, status, data, kind='text/html'):
            self.send_response(status)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            assert isinstance(self.server, HTTPServer)
            host = self.headers.get('Host')
            if (host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
                or self.headers.get('Origin') not in (None,'http://' + host)
                or self.headers.get('Sec-Fetch-Site') not in (None,'none','same-origin')):
                return self.respond(403,b'Loopback same-origin access required','text/plain')
            parts = urlsplit(self.path).path.split('/')
            try:
                if parts == ['','']:
                    data = store.library()
                elif parts == ['','style.css']:
                    return self.respond(200,STYLE.encode(),'text/css')
                elif parts == ['','app.js']:
                    return self.respond(200,SCRIPT.encode(),'text/javascript')
                elif len(parts)==3 and parts[1]=='run':
                    data = store.run(parts[2])
                elif len(parts)==4 and parts[1]=='trial':
                    data = store.trial(parts[2],parts[3])
                else:
                    return self.respond(404,b'Not found','text/plain')
                self.respond(200,data)
            except FileNotFoundError:
                self.respond(404,b'Artifact not found','text/plain')
            except (OSError,ValueError,KeyError,TypeError,RecursionError):
                self.respond(422,b'Artifact unreadable or exceeds viewing limits','text/plain')

        def do_POST(self):
            self.respond(405,b'Read-only workbench','text/plain')
    return LocalServer(('127.0.0.1',port),Handler)


def serve(root, port=4178):
    with server(root,port) as httpd:
        print(f'EvalNoise workbench: http://127.0.0.1:{httpd.server_port}',flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('Workbench stopped')
