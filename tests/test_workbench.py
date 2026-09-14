import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from evalnoise.workbench import Store, server


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.directory = self.root / 'sample'
        self.directory.mkdir()
        (self.directory/'trials').mkdir()
        self.manifest = {'config': {'name':'<script>alert(1)</script>', 'profiles':[]},
                         'status':'completed','plan':[{'trials':[{},{}]}]}
        (self.directory/'manifest.json').write_text(json.dumps(self.manifest))
        (self.directory/'trials'/'trial-one.json').write_text(json.dumps({'id':'one','task':'cpu','profile':'tight',
            'status':'passed','repeat':0,'logs':{'text':'<img src=x onerror=alert(1)>'}}))
        self.store = Store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_escapes_and_preserves_bytes(self):
        before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.rglob('*') if p.is_file()}
        for content in [self.store.library(),self.store.run('sample'),self.store.trial('sample','trial-one.json')]:
            self.assertNotIn(b'<script>alert',content)
            self.assertNotIn(b'<img src=x',content)
        self.assertIn(b'&lt;img',self.store.trial('sample','trial-one.json'))
        self.assertIn(b'Missing',self.store.run('sample'))
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.rglob('*') if p.is_file()})

    def test_traversal_and_symlinks_refused(self):
        for run in ['..','../sample','%2e%2e','a/b']:
            with self.assertRaises((ValueError,FileNotFoundError)):
                self.store.read(run,'manifest.json')
        (self.root/'alias').symlink_to(self.directory,target_is_directory=True)
        with self.assertRaises(ValueError): self.store.read('alias','manifest.json')
        (self.directory/'trial-link.json').symlink_to(self.directory/'manifest.json')
        with self.assertRaises(ValueError): self.store.read('sample','trial-link.json')

    def test_limits_and_bad_json(self):
        with patch('evalnoise.workbench.LIMIT',4):
            with self.assertRaises(ValueError): self.store.read('sample','manifest.json')
        (self.directory/'trial-bad.json').write_text('[]')
        with self.assertRaises(ValueError): self.store.read('sample','trial-bad.json')

    def test_http_boundary(self):
        httpd = server(self.root,0)
        thread = threading.Thread(target=httpd.serve_forever)
        thread.start()
        try:
            def get(path='/',headers=None,method='GET'):
                connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
                connection.request(method,path,headers=headers or {})
                response=connection.getresponse(); data=response.read(); code=response.status
                csp=response.getheader('Content-Security-Policy'); connection.close()
                return code,data,csp
            self.assertEqual(get()[0],200)
            self.assertIn("frame-ancestors 'none'",get()[2] or '')
            self.assertEqual(get('/run/sample')[0],200)
            self.assertEqual(get('/trial/sample/trial-one.json')[0],200)
            self.assertEqual(get(headers={'Host':'evil.example'})[0],403)
            self.assertEqual(get(headers={'Origin':'https://evil.example'})[0],403)
            self.assertEqual(get(headers={'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(get('/.git/config')[0],404)
            self.assertEqual(get('/api/run',method='POST')[0],405)
            self.assertEqual(get('/trial/sample/manifest.json')[0],422)
        finally:
            httpd.shutdown();thread.join();httpd.server_close()


if __name__ == '__main__':
    unittest.main()
