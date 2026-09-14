"""Transactional ownership, fencing and artifact acceptance regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from evalnoise.cluster import Client, server, worker_once
from evalnoise.coordinator import Control, ControlError, initialize
from evalnoise.investigation import canonical
from test_compare import build_run, raw_config, all_pass

IMAGE='sha256:'+'a'*64


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        raw=raw_config(tasks=1,repeats=1,profiles=[{'id':'one','cpus':1,'memory_mb':128,'timeout_s':5}])
        raw['tasks'][0]['image']=IMAGE
        self.config=raw
        initialize(self.root/'control',{'check':{'pool':'trusted','config':raw}})
        self.tokens={n:json.loads((self.root/'control'/f'{n}.json').read_text())['token'] for n in ('admin','alice','bob','worker','worker-b')}
        self.now=[100.0]
        self.control=Control(self.root/'control',clock=lambda:self.now[0],lease_seconds=3)
        path=build_run(self.root/'fixture',raw,all_pass,images={IMAGE:{'id':IMAGE,'architecture':'arm64'}})
        self.bundle={'manifest':json.loads((path/'manifest.json').read_text()),
                     'trials':[json.loads(p.read_text()) for p in (path/'trials').glob('*.json')]}

    def submit(self, owner='alice', key='request'):
        return self.control.submit(self.tokens[owner],'check',key)['job']

    def claim(self): return self.control.claim(self.tokens['worker'])

    def complete(self,lease,bundle=None):
        return self.control.complete(self.tokens['worker'],lease['job'],lease['lease'],bundle or self.bundle)

    def test_owner_isolation_roles_and_idempotency(self):
        job=self.submit()
        self.assertEqual(job,self.submit())
        self.assertNotEqual(job,self.submit('bob'))
        with self.assertRaises(ControlError): self.control.owner_jobs(self.tokens['bob'],job)
        with self.assertRaises(ControlError): self.control.owner_action(self.tokens['bob'],job,'cancel')
        with self.assertRaises(ControlError): self.control.claim(self.tokens['alice'])
        with self.assertRaises(ControlError): self.control.owner_jobs('bad')
        with self.assertRaises(ControlError): self.control.audit(self.tokens['alice'])

    def test_concurrent_claim_at_most_one_lease(self):
        self.submit()
        def call(_):
            try: return self.claim()
            except ControlError: return None
        with ThreadPoolExecutor(8) as pool: results=list(pool.map(call,range(8)))
        self.assertEqual(sum(r is not None and r['job'] is not None for r in results),1)

    def test_two_workers_claim_distinct_owner_jobs(self):
        expected={self.submit(),self.submit('bob')}
        with ThreadPoolExecutor(2) as pool:
            claims=list(pool.map(lambda name:self.control.claim(self.tokens[name]),('worker','worker-b')))
        self.assertEqual({c['job'] for c in claims},expected)
        self.assertEqual(len({c['lease'] for c in claims}),2)

    def test_completion_duplicate_and_changed_bundle_fencing(self):
        self.submit(); lease=self.claim(); receipt=self.complete(lease)
        self.assertEqual(self.complete(lease),receipt)
        modified=copy.deepcopy(self.bundle);modified['trials'][0]['logs']={'text':'new'}
        with self.assertRaises(ControlError): self.complete(lease,modified)
        self.assertEqual(receipt['mac'],hmac.new(self.control.key,canonical(receipt['payload']),hashlib.sha256).hexdigest())
        events=self.control.audit(self.tokens['admin'])['events']
        self.assertEqual(sum(e['kind']=='accepted' for e in events),1)

    def test_expired_lease_reclaim_refuses_stale_completion(self):
        self.submit(); first=self.claim(); self.now[0]+=4; second=self.claim()
        self.assertEqual(second['attempt'],2)
        with self.assertRaises(ControlError): self.complete(first)
        self.complete(second)

    def test_heartbeat_cancel_revoke_and_terminal_purge(self):
        job=self.submit(); lease=self.claim();self.now[0]+=2
        self.control.heartbeat(self.tokens['worker'],job,lease['lease'])
        self.now[0]+=2; self.complete(lease)
        self.control.owner_action(self.tokens['alice'],job,'purge')
        with self.assertRaises(ControlError): self.control.owner_jobs(self.tokens['alice'],job,artifact=True)
        other=self.submit(key='next'); second=self.claim()
        self.control.owner_action(self.tokens['alice'],other,'cancel')
        with self.assertRaises(ControlError): self.complete(second)
        self.control.revoke(self.tokens['admin'],'worker')
        with self.assertRaises(ControlError): self.claim()

    def test_worker_revocation_fences_live_lease(self):
        self.submit(); lease=self.claim();self.control.revoke(self.tokens['admin'],'worker')
        with self.assertRaises(ControlError): self.complete(lease)
        self.assertEqual(self.control.owner_jobs(self.tokens['alice'])['jobs'][0]['state'],'cancelled')

    def test_quota_reservations_count_lost_attempts(self):
        self.control.trial_quota=1;self.submit();self.claim();self.now[0]+=4
        self.assertIsNone(self.claim()['job'])
        self.assertEqual(self.control.owner_jobs(self.tokens['alice'])['jobs'][0]['state'],'failed')
        self.submit(key='two');self.submit(key='three')
        with self.assertRaises(ControlError): self.submit(key='four')

    def test_concurrent_submissions_cannot_overrun_active_quota(self):
        def submit(index):
            try:return self.submit(key='request-'+str(index))
            except ControlError as error:
                self.assertEqual(error.status,429);return None
        with ThreadPoolExecutor(8) as pool: results=list(pool.map(submit,range(10)))
        self.assertEqual(sum(r is not None for r in results),2)
        self.assertEqual(len(self.control.owner_jobs(self.tokens['alice'])['jobs']),2)

    def test_storage_quota_refusal_does_not_accept_or_discard_evidence(self):
        self.submit();lease=self.claim();self.control.artifact_quota=1
        with self.assertRaises(ControlError) as caught:self.complete(lease)
        self.assertEqual(caught.exception.status,429)
        self.assertEqual(self.control.owner_jobs(self.tokens['alice'])['jobs'][0]['state'],'leased')
        self.assertFalse(any(e['kind']=='accepted' for e in self.control.audit(self.tokens['admin'])['events']))
        self.control.artifact_quota=64*1024*1024;self.complete(lease)

    def test_expiration_retries_stop_at_three_attempts(self):
        self.submit()
        for attempt in range(1,4):
            self.assertEqual(self.claim()['attempt'],attempt);self.now[0]+=4
        self.assertIsNone(self.claim()['job'])
        self.assertEqual(self.control.owner_jobs(self.tokens['alice'])['jobs'][0]['state'],'failed')

    def test_restart_keeps_lease_and_audit_tamper_detection(self):
        self.submit();lease=self.claim()
        self.control=Control(self.root/'control',clock=lambda:self.now[0],lease_seconds=3)
        self.complete(lease);self.assertTrue(self.control.audit(self.tokens['admin'])['valid'])
        with closing(sqlite3.connect(self.root/'control'/'control.sqlite')) as db, db:
            db.execute("UPDATE events SET payload='{}' WHERE seq=1")
        with self.assertRaises(ControlError):self.control.audit(self.tokens['admin'])

    def test_modified_artifact_is_not_served_under_an_old_receipt(self):
        job=self.submit();self.complete(self.claim())
        with closing(sqlite3.connect(self.root/'control'/'control.sqlite')) as db, db:
            db.execute("UPDATE jobs SET bundle='{}' WHERE id=?",(job,))
        with self.assertRaises(ControlError):self.control.owner_jobs(self.tokens['alice'],job,artifact=True)
        with self.assertRaises(ControlError):self.control.audit(self.tokens['admin'])

    def test_worker_pool_mismatch_is_not_scheduled(self):
        self.submit()
        with closing(sqlite3.connect(self.root/'control'/'control.sqlite')) as db, db:
            db.execute("UPDATE principals SET pool='another-pool' WHERE id='worker'")
        self.assertIsNone(self.claim()['job'])

    def test_artifact_contract_rejects_wrong_image_seed_missing_and_status(self):
        self.submit();lease=self.claim()
        for mutation in ('image','seed','missing','config','unstable','status'):
            bundle=copy.deepcopy(self.bundle)
            if mutation=='image': bundle['manifest']['images'][IMAGE]['id']='sha256:'+'b'*64
            elif mutation=='seed': bundle['trials'][0]['seed']+=1
            elif mutation=='missing': bundle['trials']=[]
            elif mutation=='config': bundle['manifest']['config']['seed']+=1
            elif mutation=='unstable': bundle['manifest']['engine_identity_final']['stable']=False
            else: bundle['trials'][0]['status']='invented'
            with self.subTest(mutation=mutation),self.assertRaises(ControlError):self.complete(lease,bundle)
        self.complete(lease)

    def test_only_reviewed_images_no_agent_catalog(self):
        raw=copy.deepcopy(self.config);raw['tasks'][0]['image']='python:latest'
        with self.assertRaises(ControlError):initialize(self.root/'bad',{'check':{'pool':'trusted','config':raw}})

    def test_http_roles_and_worker_end_to_end(self):
        httpd=server(self.control,0);thread=threading.Thread(target=httpd.serve_forever);thread.start()
        try:
            url=f'http://127.0.0.1:{httpd.server_port}'
            owner=Client(url,self.tokens['alice']);worker=Client(url,self.tokens['worker'])
            owner.request('/submit',{'catalog':'check','request_key':'one'})
            def executor(*args,**kwargs): return self.root/'fixture'
            result=worker_once(worker,[IMAGE],self.root,backend=object(),executor=executor)
            self.assertEqual(result['state'],'completed')
            artifact=owner.request('/artifact/'+result['job'])
            self.assertEqual(artifact['bundle'],self.bundle)
            with self.assertRaises(ControlError): Client(url,self.tokens['bob']).request('/artifact/'+result['job'])
            with self.assertRaises(ControlError): owner.request('/claim',{})
            with self.assertRaises(ControlError): owner.request('/submit',{'catalog':'check','request_key':'two','command':'oops'})
        finally: httpd.shutdown();thread.join();httpd.server_close()

    def test_local_worker_allowlist_cannot_be_overridden_by_server(self):
        self.submit()
        class Transport:
            def request(inner,path,payload):
                if path=='/claim':return self.claim()
                if path=='/fail':return self.control.fail(self.tokens['worker'],**payload)
                raise AssertionError(path)
        with self.assertRaises(ControlError):worker_once(Transport(),[],self.root)
        self.assertEqual(self.control.owner_jobs(self.tokens['alice'])['jobs'][0]['state'],'failed')
