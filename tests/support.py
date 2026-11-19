"""测试仅访问合成来源及接收端，不是采集产品验收。"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from source.app import Site
from source.common import server

class SourceTest(unittest.TestCase):
    def setUp(self):
        self.site=Site();self.httpd=server(self.site)
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.httpd.server_port)
    def tearDown(self):
        self.httpd.shutdown();self.httpd.server_close();self.thread.join()
    def request(self,path,data=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        try:r=urllib.request.urlopen(req,timeout=5)
        except urllib.error.HTTPError as e:r=e
        raw=r.read();return r.status,raw,r.headers
    def get(self,path):
        status,raw,_=self.request(path);self.assertEqual(status,200,raw);return json.loads(raw)
    def control(self,event,**kwargs):
        status,raw,_=self.request('/__control',{'event':event,**kwargs});self.assertEqual(status,200,raw)
    def browser(self):
        from playwright.sync_api import sync_playwright
        self.pw=sync_playwright().start();self.chromium=self.pw.chromium.launch(headless=True,args=['--no-sandbox'])
        self.context=self.chromium.new_context(accept_downloads=True);self.tab=self.context.new_page()
        self.addCleanup(self.pw.stop);self.addCleanup(self.chromium.close);self.addCleanup(self.context.close)
        return self.tab
