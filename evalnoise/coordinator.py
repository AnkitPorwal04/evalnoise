"""Single-host, authenticated leased queue. Workers are trusted execution principals."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from .config import parse, plan
from .compare import RESOLVED_STATUSES
from .investigation import canonical, digest
from .report import summarize

IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
NAME = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
MAX_BUNDLE = 16 * 1024 * 1024


class ControlError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def token_hash(token):
    if not isinstance(token, str) or len(token) > 256:
        raise ControlError('Invalid credential', 401)
    return hashlib.sha256(token.encode()).hexdigest()


def reviewed(config):
    experiment = parse(config)
    if config.get('provider') is not None or any(t.agent is not None for t in experiment.tasks):
        raise ControlError('Catalog permits container workloads only, not model providers')
    images = {t.image for t in experiment.tasks}
    images.update(t.verifier.image for t in experiment.tasks if t.verifier)
    if not all(IMAGE.fullmatch(i) for i in images):
        raise ControlError('Catalog images must be immutable sha256 image IDs')
    return experiment, sorted(images)


def initialize(directory, catalog):
    """Provision local credentials once; never return or print their values."""
    if not isinstance(catalog, dict) or not 1 <= len(catalog) <= 100:
        raise ControlError('Expected 1–100 reviewed catalog entries')
    entries = []
    for name, entry in catalog.items():
        if not NAME.fullmatch(name) or set(entry) != {'pool', 'config'} or not NAME.fullmatch(entry['pool']):
            raise ControlError('Invalid catalog entry')
        experiment, images = reviewed(entry['config'])
        entries.append((name, entry['pool'], canonical(experiment.data()).decode(), images))
    root = Path(directory)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    (root / 'key').write_bytes(secrets.token_bytes(32)); os.chmod(root / 'key', 0o600)
    db = sqlite3.connect(root / 'control.sqlite')
    try:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE principals(id TEXT PRIMARY KEY, role TEXT, token TEXT UNIQUE, active INTEGER,
                                pool TEXT, images TEXT);
        CREATE TABLE catalog(id TEXT PRIMARY KEY, pool TEXT, config TEXT);
        CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT, catalog TEXT, request_key TEXT,
                          state TEXT, attempt INTEGER, worker TEXT, lease TEXT, expires REAL,
                          bundle BLOB, receipt TEXT, created REAL, UNIQUE(owner, request_key));
        CREATE TABLE usage(owner TEXT PRIMARY KEY, trials INTEGER NOT NULL);
        CREATE TABLE events(seq INTEGER PRIMARY KEY, payload TEXT, previous TEXT, signature TEXT);
        ''')
        for name, pool, config, _ in entries:
            db.execute('INSERT INTO catalog VALUES(?,?,?)', (name, pool, config))
        images = sorted({i for _, _, _, values in entries for i in values})
        pools = {pool for _, pool, _, _ in entries}
        if len(pools) != 1:
            raise ControlError('Local provisioning requires one reviewed pool')
        for name, role in [('admin', 'admin'), ('alice', 'owner'), ('bob', 'owner'), ('worker', 'worker'), ('worker-b', 'worker')]:
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO principals VALUES(?,?,?,?,?,?)',
                       (name, role, token_hash(token), 1, next(iter(pools)), json.dumps(images)))
            path = root / f'{name}.json'
            path.write_bytes(canonical({'principal': name, 'token': token, 'images': images}))
            os.chmod(path, 0o600)
        db.commit()
    finally:
        db.close()
        os.chmod(root / 'control.sqlite', 0o600)
    return {'directory': str(root), 'principals': ['admin', 'alice', 'bob', 'worker', 'worker-b']}


class Control:
    def __init__(self, directory, *, clock=time.time, lease_seconds=30, trial_quota=1000, artifact_quota=64*1024*1024):
        self.root = Path(directory).resolve(strict=True)
        if self.root.stat().st_mode & 0o077:
            raise ControlError('Coordinator directory must be private (0700)')
        self.key = (self.root / 'key').read_bytes()
        if len(self.key) != 32 or not (self.root / 'control.sqlite').is_file():
            raise ControlError('Invalid coordinator state')
        self.clock, self.lease_seconds, self.trial_quota = clock, lease_seconds, trial_quota
        self.artifact_quota = artifact_quota

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.root / 'control.sqlite', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def principal(self, db, token, role):
        row = db.execute('SELECT * FROM principals WHERE token=? AND active=1', (token_hash(token),)).fetchone()
        if row is None:
            raise ControlError('Invalid credential', 401)
        if row['role'] != role:
            raise ControlError('Role not permitted', 403)
        return row

    def event(self, db, kind, **fields):
        last = db.execute('SELECT seq,signature FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        previous, seq = (last['signature'], last['seq'] + 1) if last else ('0' * 64, 1)
        payload = canonical({'seq': seq, 'time': self.clock(), 'kind': kind, **fields}).decode()
        signature = hmac.new(self.key, (previous + payload).encode(), hashlib.sha256).hexdigest()
        db.execute('INSERT INTO events VALUES(?,?,?,?)', (seq, payload, previous, signature))

    def submit(self, token, catalog, request_key):
        if not isinstance(request_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', request_key):
            raise ControlError('Invalid idempotency key', 400)
        with self.transaction() as db:
            owner = self.principal(db, token, 'owner')['id']
            old = db.execute('SELECT * FROM jobs WHERE owner=? AND request_key=?', (owner, request_key)).fetchone()
            if old:
                if old['catalog'] != catalog:
                    raise ControlError('Idempotency key belongs to another catalog item')
                return {'job': old['id'], 'state': old['state']}
            if not db.execute('SELECT id FROM catalog WHERE id=?', (catalog,)).fetchone():
                raise ControlError('Unknown catalog item', 404)
            active = db.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('queued','leased')", (owner,)).fetchone()[0]
            total = db.execute('SELECT count(*) FROM jobs WHERE owner=?', (owner,)).fetchone()[0]
            if active >= 2 or total >= 100:
                raise ControlError('Owner job quota exhausted', 429)
            job = secrets.token_hex(16)
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (job, owner, catalog, request_key, 'queued', 0, None, None, None, None, None, self.clock()))
            self.event(db, 'submitted', job=job, owner=owner, catalog=catalog)
            return {'job': job, 'state': 'queued'}

    def claim(self, token):
        with self.transaction() as db:
            worker = self.principal(db, token, 'worker')
            now = self.clock()
            for row in db.execute("SELECT * FROM jobs WHERE state='leased' AND expires<=?", (now,)).fetchall():
                state = 'failed' if row['attempt'] >= 3 else 'queued'
                db.execute('UPDATE jobs SET state=?,lease=NULL WHERE id=?', (state, row['id']))
                self.event(db, 'lease_expired', job=row['id'], attempt=row['attempt'], state=state)
            if db.execute("SELECT id FROM jobs WHERE state='leased' AND worker=?", (worker['id'],)).fetchone():
                raise ControlError('Worker already holds a lease')
            rows = db.execute("SELECT jobs.*, catalog.config,catalog.pool FROM jobs JOIN catalog ON jobs.catalog=catalog.id WHERE state='queued' ORDER BY created,id").fetchall()
            for row in rows:
                config = json.loads(row['config']); experiment, images = reviewed(config)
                if row['pool'] != worker['pool'] or not set(images).issubset(json.loads(worker['images'])):
                    continue
                count = len(experiment.tasks) * len(experiment.profiles) * experiment.repeats
                used = db.execute('SELECT trials FROM usage WHERE owner=?', (row['owner'],)).fetchone()
                if (used[0] if used else 0) + count > self.trial_quota:
                    db.execute("UPDATE jobs SET state='failed' WHERE id=?", (row['id'],))
                    self.event(db, 'trial_quota_refused', job=row['id'])
                    continue
                db.execute('INSERT INTO usage VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET trials=trials+excluded.trials', (row['owner'], count))
                lease = secrets.token_urlsafe(32); attempt = row['attempt'] + 1
                db.execute("UPDATE jobs SET state='leased',attempt=?,worker=?,lease=?,expires=? WHERE id=?",
                           (attempt, worker['id'], token_hash(lease), now+self.lease_seconds, row['id']))
                self.event(db, 'leased', job=row['id'], worker=worker['id'], attempt=attempt, reserved_trials=count)
                return {'job': row['id'], 'attempt': attempt, 'lease': lease, 'lease_seconds': self.lease_seconds,
                        'config': config, 'config_sha256': digest(config)}
            return {'job': None}

    def leased(self, db, worker, job, lease):
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if (row is None or row['state'] != 'leased' or row['worker'] != worker['id']
                or row['expires'] <= self.clock() or not hmac.compare_digest(row['lease'], token_hash(lease))):
            raise ControlError('Lease is expired, cancelled, or superseded')
        return row

    def heartbeat(self, token, job, lease):
        with self.transaction() as db:
            worker = self.principal(db, token, 'worker')
            self.leased(db, worker, job, lease)
            expires = self.clock() + self.lease_seconds
            db.execute('UPDATE jobs SET expires=? WHERE id=?', (expires, job))
            return {'expires': expires}

    def complete(self, token, job, lease, bundle):
        encoded = canonical(bundle)
        if len(encoded) > MAX_BUNDLE:
            raise ControlError('Artifact exceeds 16 MiB', 413)
        with self.transaction() as db:
            worker = self.principal(db, token, 'worker')
            old = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
            if old and old['state'] == 'completed' and old['worker'] == worker['id'] and hmac.compare_digest(old['lease'], token_hash(lease)):
                if json.loads(old['receipt'])['payload']['bundle_sha256'] != digest(bundle):
                    raise ControlError('A different result is already accepted')
                return json.loads(old['receipt'])
            row = self.leased(db, worker, job, lease)
            config = json.loads(db.execute('SELECT config FROM catalog WHERE id=?', (row['catalog'],)).fetchone()[0])
            validate_bundle(bundle, config)
            self.leased(db, worker, job, lease)
            occupied = db.execute('SELECT coalesce(sum(length(bundle)),0) FROM jobs WHERE owner=?', (row['owner'],)).fetchone()[0]
            if occupied + len(encoded) > self.artifact_quota:
                raise ControlError('Owner artifact quota exhausted', 429)
            payload = {'job': job, 'owner': row['owner'], 'worker': worker['id'], 'attempt': row['attempt'],
                       'bundle_sha256': digest(bundle), 'manifest_sha256': digest(bundle['manifest']),
                       'config_sha256': digest(config), 'accepted_at': self.clock()}
            receipt = {'kind': 'coordinator_hmac_sha256', 'payload': payload,
                       'mac': hmac.new(self.key, canonical(payload), hashlib.sha256).hexdigest()}
            db.execute("UPDATE jobs SET state='completed',bundle=?,receipt=? WHERE id=?", (encoded, canonical(receipt).decode(), job))
            self.event(db, 'accepted', **payload)
            return receipt

    def fail(self, token, job, lease):
        with self.transaction() as db:
            worker = self.principal(db, token, 'worker'); self.leased(db, worker, job, lease)
            db.execute("UPDATE jobs SET state='failed',lease=NULL WHERE id=?", (job,))
            self.event(db, 'worker_failed', job=job, worker=worker['id'])
            return {'state': 'failed'}

    def owner_jobs(self, token, job=None, artifact=False):
        with self.transaction() as db:
            owner = self.principal(db, token, 'owner')['id']
            rows = db.execute('SELECT * FROM jobs WHERE owner=? ORDER BY created', (owner,)).fetchall()
            if job is not None:
                rows = [row for row in rows if row['id'] == job]
                if not rows: raise ControlError('Job not found', 404)
            if artifact:
                if job is None or rows[0]['bundle'] is None: raise ControlError('Artifact not found', 404)
                self.check_receipt(rows[0])
                return {'bundle': json.loads(rows[0]['bundle']), 'receipt': json.loads(rows[0]['receipt'])}
            return {'jobs': [{k: r[k] for k in ('id','owner','catalog','state','attempt','worker','created')} for r in rows]}

    def check_receipt(self, row):
        receipt=json.loads(row['receipt']);payload=receipt['payload']
        expected=hmac.new(self.key,canonical(payload),hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(expected,receipt['mac']) or payload['job']!=row['id']
                or payload['owner']!=row['owner'] or payload['worker']!=row['worker'] or payload['attempt']!=row['attempt']
                or (row['bundle'] is not None and payload['bundle_sha256']!=digest(json.loads(row['bundle'])))):
            raise ControlError('Artifact receipt verification failed')

    def owner_action(self, token, job, action):
        if action not in ('cancel', 'purge'): raise ControlError('Unknown action', 400)
        with self.transaction() as db:
            owner = self.principal(db, token, 'owner')['id']
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job, owner)).fetchone()
            if row is None: raise ControlError('Job not found', 404)
            if action == 'cancel':
                if row['state'] not in ('queued','leased'): raise ControlError('Job is terminal')
                db.execute("UPDATE jobs SET state='cancelled',lease=NULL WHERE id=?", (job,))
            else:
                if row['state'] != 'completed' or row['bundle'] is None: raise ControlError('No terminal artifact to purge')
                db.execute("UPDATE jobs SET state='purged',bundle=NULL,lease=NULL WHERE id=?", (job,))
            self.event(db, action, job=job, owner=owner)
            return {'action': action, 'job': job}

    def revoke(self, token, principal):
        with self.transaction() as db:
            admin = self.principal(db, token, 'admin')
            if principal == admin['id']: raise ControlError('Cannot revoke the active administrator')
            if not db.execute('SELECT id FROM principals WHERE id=?', (principal,)).fetchone(): raise ControlError('Principal not found',404)
            db.execute('UPDATE principals SET active=0 WHERE id=?', (principal,))
            db.execute("UPDATE jobs SET state='cancelled',lease=NULL WHERE (owner=? OR worker=?) AND state IN ('queued','leased')", (principal, principal))
            self.event(db, 'revoked', principal=principal)
            return {'revoked': principal}

    def audit(self, token):
        with self.transaction() as db:
            self.principal(db, token, 'admin')
            for row in db.execute('SELECT * FROM jobs WHERE receipt IS NOT NULL'):
                self.check_receipt(row)
            rows = db.execute('SELECT * FROM events ORDER BY seq').fetchall()
            previous = '0'*64
            for index, row in enumerate(rows, 1):
                expected = hmac.new(self.key, (previous+row['payload']).encode(), hashlib.sha256).hexdigest()
                if row['seq'] != index or row['previous'] != previous or not hmac.compare_digest(expected,row['signature']):
                    raise ControlError('Audit chain verification failed')
                previous = row['signature']
            return {'valid': True, 'head': previous, 'events': [json.loads(r['payload']) for r in rows],
                    'scope': 'Symmetric integrity, not public signing or protection from administrator rollback'}


def validate_bundle(bundle, config):
    try:
        if set(bundle) != {'manifest','trials'}: raise ValueError('Unexpected bundle fields')
        manifest, trials = bundle['manifest'], bundle['trials']
        experiment, images = reviewed(config)
        if canonical(manifest['config']) != canonical(config) or manifest['plan'] != plan(experiment):
            raise ValueError('Catalog config or schedule mismatch')
        if manifest['status'] != 'completed' or manifest['engine_identity_final']['stable'] is not True:
            raise ValueError('Incomplete or unstable engine result')
        if set(manifest['images']) != set(images) or any(manifest['images'][i]['id'] != i for i in images):
            raise ValueError('Unapproved image identity')
        if len(trials) != len(experiment.tasks)*len(experiment.profiles)*experiment.repeats:
            raise ValueError('Missing trials')
        expected = {t['id']: t for batch in manifest['plan'] for t in batch['trials']}
        if any(t.get('cleanup_error') or t['status'] not in RESOLVED_STATUSES
               or t['seed'] != expected[t['id']]['seed'] for t in trials):
            raise ValueError('Unresolved or unclean trial')
        summarize(manifest, trials)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ControlError('Invalid artifact: '+str(error), 400) from error
