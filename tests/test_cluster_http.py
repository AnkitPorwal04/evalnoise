"""Transport abuse, lease-loss cancellation and a real Docker worker check."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import time
import unittest

from evalnoise.cluster import Client, server, worker_once
from evalnoise.coordinator import Control, ControlError, initialize
from evalnoise.docker import Docker
import test_coordinator as fixtures
IMAGE=fixtures.IMAGE


class TransportTests(unittest.TestCase):
    root: Path
    control: Control
    tokens: dict[str,str]
    bundle: dict
    setUp=fixtures.CoordinatorTests.setUp
    submit=fixtures.CoordinatorTests.submit
    claim=fixtures.CoordinatorTests.claim
    def test_http_security_headers_methods_and_size(self):
        httpd=server(self.control,0);thread=threading.Thread(target=httpd.serve_forever);thread.start()
        try:
            for path, headers, method, payload, expected in [
                ('/jobs',{},'GET',None,403),
                ('/jobs',{'Authorization':'Bearer bad'},'GET',None,401),
                ('/jobs',{'Origin':'https://evil.test'},'GET',None,403),
                ('/jobs',{'Host':'evil.test'},'GET',None,403),
                ('/jobs',{'Sec-Fetch-Site':'cross-site'},'GET',None,403),
                ('/submit',{},'POST',b'{"catalog":[],"request_key":"x"}',400),
                ('/submit',{},'POST',b'{"catalog":"check","catalog":"check","request_key":"x"}',400),
                ('/submit',{'Content-Length':str(20*1024*1024)},'POST',b'{}',413),
                ('/../control.sqlite',{},'GET',None,404),
                ('/execute',{},'POST',b'{}',404),
            ]:
                connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=10)
                base={'Authorization':'Bearer '+self.tokens['alice'],'Content-Type':'application/json'}
                if expected==403 and not headers: base.pop('Authorization')
                base.update(headers)
                connection.request(method,path,payload,base)
                response=connection.getresponse();response.read()
                self.assertEqual(response.status,expected,(path,headers))
                self.assertEqual(response.getheader('Cache-Control'),'no-store');connection.close()
        finally:httpd.shutdown();thread.join();httpd.server_close()

    def test_worker_stops_on_heartbeat_rejection(self):
        self.submit(); observed=[]
        class Transport:
            def request(inner,path,payload):
                if path=='/claim':return self.claim()
                if path=='/heartbeat':raise ControlError('Lease expired')
                if path=='/fail':return self.control.fail(self.tokens['worker'],**payload)
                raise AssertionError('Completion must not be submitted')
        def executor(*args,stop,**kwargs):
            observed.append(stop.wait(5));return self.root/'fixture'
        with self.assertRaises(ControlError):worker_once(Transport(),[IMAGE],self.root,backend=object(),executor=executor)
        self.assertEqual(observed,[True])
        self.assertFalse(any(t.name=='evalnoise-worker-heartbeat' for t in threading.enumerate()))

    def test_killed_claiming_process_releases_no_accepted_result(self):
        self.control=Control(self.root/'control',lease_seconds=1)
        self.submit()
        httpd=server(self.control,0);thread=threading.Thread(target=httpd.serve_forever);thread.start()
        url=f'http://127.0.0.1:{httpd.server_port}'
        script="import os,json,time; from evalnoise.cluster import Client; print(json.dumps(Client(os.environ['TEST_URL'],os.environ['TEST_TOKEN']).request('/claim',{})),flush=True); time.sleep(30)"
        process=subprocess.Popen([sys.executable,'-c',script],env={**os.environ,'TEST_URL':url,'TEST_TOKEN':self.tokens['worker']},stdout=subprocess.PIPE,text=True)
        assert process.stdout is not None
        try:
            old=json.loads(process.stdout.readline());process.kill();process.wait(timeout=5)
            time.sleep(1.1)
            successor=Client(url,self.tokens['worker-b']);new=successor.request('/claim',{})
            self.assertEqual(new['job'],old['job']);self.assertEqual(new['attempt'],2)
            with self.assertRaises(ControlError):
                Client(url,self.tokens['worker']).request('/complete',{'job':old['job'],'lease':old['lease'],'bundle':self.bundle})
            successor.request('/complete',{'job':new['job'],'lease':new['lease'],'bundle':self.bundle})
            self.assertEqual(sum(e['kind']=='accepted' for e in self.control.audit(self.tokens['admin'])['events']),1)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close();httpd.shutdown();thread.join();httpd.server_close()


@unittest.skipUnless(os.environ.get('EVALNOISE_DOCKER_TESTS')=='1','Docker opt-in required')
class RealWorkerTests(unittest.TestCase):
    def test_two_owners_real_worker_acceptance_and_isolation(self):
        docker=Docker(); docker.doctor(); image=docker.image('evalnoise-workloads:local')['id']
        config={'schema_version':1,'name':'cluster-validation','seed':31,'repeats':1,
                'tasks':[{'id':'cpu','image':image,'command':['python','/opt/evalnoise/workload.py','cpu']}],
                'profiles':[{'id':'standard','cpus':1,'memory_mb':128,'timeout_s':15}]}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);initialize(root/'control',{'cpu':{'pool':'trusted','config':config}})
            tokens={name:json.loads((root/'control'/f'{name}.json').read_text())['token'] for name in ('alice','bob','worker','admin')}
            control=Control(root/'control');httpd=server(control,0)
            thread=threading.Thread(target=httpd.serve_forever);thread.start()
            try:
                url=f'http://127.0.0.1:{httpd.server_port}'
                for name in ('alice','bob'):
                    owner=Client(url,tokens[name]);job=owner.request('/submit',{'catalog':'cpu','request_key':'same-key'})['job']
                    result=worker_once(Client(url,tokens['worker']),[image],root/'runs')
                    self.assertEqual(result['job'],job)
                    artifact=owner.request('/artifact/'+job)
                    self.assertEqual(artifact['bundle']['trials'][0]['status'],'passed')
                    self.assertEqual(artifact['receipt']['payload']['owner'],name)
                    other='bob' if name=='alice' else 'alice'
                    with self.assertRaises(ControlError):Client(url,tokens[other]).request('/artifact/'+job)
                self.assertEqual(sum(e['kind']=='accepted' for e in control.audit(tokens['admin'])['events']),2)
            finally:httpd.shutdown();thread.join();httpd.server_close()
