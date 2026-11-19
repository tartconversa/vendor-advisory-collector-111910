"""本地合成来源 HTTP 基础设施；不含采集客户端。"""
import argparse
import copy
import hashlib
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

def digest(data):
    return hashlib.sha256(data).hexdigest()

def page(title, body, script=''):
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><title>'+title+
            '</title><style>body{font:16px sans-serif;max-width:1000px;margin:32px auto}label,button,select,input{margin:8px}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:9px}pre{white-space:pre-wrap}a{margin:8px}</style><h1>'+title+'</h1>'+body+'<script>'+script+'</script></html>')

def escaped(value):
    return html.escape(str(value), quote=True)

def dump(value):
    return json.dumps(value, ensure_ascii=False)

class Reply:
    def __init__(self, body, status=200, mime='application/json; charset=utf-8', headers=None):
        self.body = body if isinstance(body, bytes) else (dump(body) if not isinstance(body,str) else body).encode()
        self.status, self.mime, self.headers = status, mime, headers or {}

class StreamReply:
    def __init__(self,chunks,mime='text/event-stream'):
        self.chunks,self.mime=chunks,mime

def web(body, status=200):
    return Reply(body,status,'text/html; charset=utf-8')

class Provider:
    def __init__(self, state_path=None):
        self.lock=threading.RLock()
        self.state_path=Path(state_path) if state_path else None
        self.state=self.initial()
        if self.state_path and self.state_path.exists():
            self.state=json.loads(self.state_path.read_text(encoding='utf-8'))
    def save(self):
        if self.state_path:
            self.state_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.state_path.with_suffix('.tmp')
            tmp.write_text(dump(self.state),encoding='utf-8');tmp.replace(self.state_path)
    def control(self, data):
        if data.get('event')=='reset': self.state=self.initial()
        else: return self.event(data)
        return {'ok':True}
    def event(self, data):
        return Reply({'error':'unknown_event'},400)
    def get(self, path, query, headers):
        return Reply({'error':'not_found'},404)
    def post(self, path, data, headers):
        return Reply({'error':'not_found'},404)

def server(provider, port=0, host='127.0.0.1'):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def do_GET(self): self.handle_request(False)
        def do_POST(self): self.handle_request(True)
        def handle_request(self, posting):
            url=urlsplit(self.path);q={k:v[-1] for k,v in parse_qs(url.query,keep_blank_values=True).items()}
            try:
                with provider.lock:
                    if posting:
                        size=int(self.headers.get('Content-Length','0'))
                        if size>2_000_000: result=Reply({'error':'too_large'},413)
                        else:
                            raw=self.rfile.read(size)
                            data=json.loads(raw) if self.headers.get('Content-Type','').startswith('application/json') else {k:v[-1] for k,v in parse_qs(raw.decode(),keep_blank_values=True).items()}
                            result=provider.control(data) if url.path=='/__control' else provider.post(url.path,data,self.headers)
                            provider.save()
                    elif url.path=='/health': result={'status':'ok','kind':'synthetic-source'}
                    else: result=provider.get(url.path,q,self.headers)
                    if result is None: result=Reply({'error':'not_found'},404)
                    if not isinstance(result,(Reply,StreamReply)): result=Reply(result)
            except (ValueError,KeyError,TypeError) as exc:
                result=Reply({'error':'bad_request','detail':str(exc)},400)
            if isinstance(result,StreamReply):
                self.send_response(200);self.send_header('Content-Type',result.mime)
                self.send_header('Transfer-Encoding','chunked');self.send_header('Cache-Control','no-store');self.end_headers()
                try:
                    for chunk in result.chunks:
                        self.wfile.write(('%x\r\n'%len(chunk)).encode()+chunk+b'\r\n');self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
                return
            self.send_response(result.status);self.send_header('Content-Type',result.mime)
            for k,v in result.headers.items():self.send_header(k,v)
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(result.body)));self.end_headers()
            try:self.wfile.write(result.body);self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
    return ThreadingHTTPServer((host,port),Handler)

def main(provider_type):
    parser=argparse.ArgumentParser(description='本地合成来源服务')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--state',help='可选的来源状态文件，放在源码目录外')
    args=parser.parse_args();httpd=server(provider_type(args.state),args.port,args.host)
    print('来源地址 http://'+args.host+':'+str(httpd.server_port),flush=True)
    try:httpd.serve_forever()
    except KeyboardInterrupt:pass
    finally:httpd.server_close()
