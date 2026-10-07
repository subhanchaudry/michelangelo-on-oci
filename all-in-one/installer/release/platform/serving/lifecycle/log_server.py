"""Read-only log access behind private Studio forwarding; no public listener route.
Authentication/project authorization remain an explicit organization rollout gate.
"""
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from urllib.parse import urlsplit
import boto3

PROJECTS={'oci-ranking-showcase','oci-movielens-showcase','oci-fraud-anomaly-showcase','oci-california-housing-showcase','oci-bert-cola-showcase'}
S3=None
MAX_LOG=8*1024**2

def read_object(key, maximum):
    result=S3.get_object(Bucket=os.environ['AWS_S3_BUCKET'],Key=key)
    body=result['Body']
    try:
        if not 0<result.get('ContentLength',0)<=maximum:raise ValueError('Log exceeds size limit')
        data=body.read(maximum+1)
        if len(data)>maximum:raise ValueError('Log exceeds size limit')
        return data
    finally:body.close()


def read_log(path):
    match=re.fullmatch(r'/pilot-logs/([a-z0-9-]+)/([a-z0-9-]+)',path)
    if not match or match[1] not in PROJECTS:raise ValueError('Unknown log route')
    project,run=match.groups()
    prefix='studio-demos/lifecycle/'+project+'/logs/'
    record=json.loads(read_object(prefix+'index/'+run+'.json',16*1024))
    if record.get('project')!=project or record.get('source_run')!=run:raise ValueError('Log provenance mismatch')
    digest=record.get('sha256','')
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid log checksum')
    expected='s3://'+os.environ['AWS_S3_BUCKET']+'/'+prefix+digest+'.log'
    if record.get('uri')!=expected:raise ValueError('Invalid log reference')
    data=read_object(prefix+digest+'.log',MAX_LOG)
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Log integrity check failed')
    return data

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route=urlsplit(self.path)
        browser_view=route.query=='format=html'
        if self.path=='/healthz':code,data=200,b'ok\n'
        else:
            try:
                if route.query and not browser_view:raise ValueError('Unknown log format')
                code,data=200,read_log(route.path)
            except Exception:
                # Do not leak object-storage details or credentials to the browser.
                code,data=404,b'Log not available yet, or its integrity check failed.\n'
        if browser_view:
            data=('<!doctype html><html><head><meta charset="utf-8"><title>Michelangelo task log</title>'
                  '<style>body{font:15px system-ui;margin:32px;color:#202124}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px monospace;background:#f4f6fa;padding:20px;border-radius:8px}</style>'
                  '</head><body><h1>Persisted task log</h1><pre>'+html.escape(data.decode('utf-8',errors='replace'))+'</pre></body></html>').encode()
        self.send_response(code)
        self.send_header('Content-Type',('text/html' if browser_view else 'text/plain')+'; charset=utf-8')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)

if __name__=='__main__':
    S3=boto3.client('s3',endpoint_url=os.environ['AWS_ENDPOINT_URL'],region_name=os.environ['AWS_REGION'])
    ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
