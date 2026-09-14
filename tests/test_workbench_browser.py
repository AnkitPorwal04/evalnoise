"""Opt-in Chromium regressions, including evidence updates and download semantics."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from evalnoise.workbench import server


@unittest.skipUnless(os.environ.get('EVALNOISE_BROWSER_TESTS')=='1','Browser opt-in required')
class BrowserTests(unittest.TestCase):
    def test_complete_investigation_flow_and_live_snapshot(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('first','second'):
                run=root/name; (run/'trials').mkdir(parents=True)
                (run/'manifest.json').write_text(json.dumps({'config':{'name':name,'profiles':[],'seed':1},'status':'running','plan':[{'trials':[{},{}]}],'task_contracts':{'t':name}}))
                (run/'trials'/'one.json').write_text(json.dumps({'id':'one','task':'cpu','profile':'small','status':'passed','repeat':0,'telemetry':[{'received_elapsed_s':1,'raw':{'memory_stats':{'usage':10000}}}],'logs':{'text':'<script>window.leaked=true</script>'}}))
            httpd=server(root,0); thread=threading.Thread(target=httpd.serve_forever);thread.start()
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True,executable_path=os.environ.get('EVALNOISE_CHROME'))
                    page=browser.new_page(viewport={'width':1440,'height':900}); errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    base=f'http://127.0.0.1:{httpd.server_port}'
                    page.goto(base)
                    page.locator('summary').first.click()
                    page.select_option('#baseline','first');page.select_option('#candidate','second');page.click('#compare-go')
                    self.assertIn('incompatible',page.inner_text('main'))
                    page.goto(base+'/run/first')
                    page.select_option('[data-filter=status]','passed');page.check('#live')
                    (root/'first'/'trials'/'two.json').write_text(json.dumps({'id':'two','task':'ram','profile':'large','status':'oom_killed','repeat':0}))
                    expect(page.locator('#records tbody tr')).to_have_count(2, timeout=15000)
                    self.assertEqual(page.locator('#records tbody tr:visible').count(),1)
                    self.assertTrue(page.is_checked('#live'))
                    self.assertEqual(page.input_value('[data-filter=status]'),'passed')
                    with page.expect_download() as download:
                        page.click('[data-export]')
                    exported=json.loads(Path(download.value.path()).read_text())
                    self.assertEqual((exported['recorded_count'],exported['selected_count']),(2,1))
                    page.click('#records tbody tr:visible a')
                    self.assertTrue(page.locator('svg').count()>0)
                    self.assertIsNone(page.evaluate('window.leaked'))
                    for width in (1440,390):
                        page.set_viewport_size({'width':width,'height':900})
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth'))
                    page.goto(base+'/run/second')
                    self.assertEqual(page.input_value('[data-filter=status]'),'')
                    self.assertEqual(page.locator('#records tbody tr').count(),1)
                    self.assertEqual(errors,[])
                    browser.close()
            finally:
                httpd.shutdown();thread.join();httpd.server_close()
