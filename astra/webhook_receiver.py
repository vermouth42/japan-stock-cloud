"""Isolated HTTPS receiver for integration tests; public hosting not configured.

Mutual TLS chain verification AND peer identity are mandatory. Authentication
headers or JSON flags never authenticate the caller. No proxy-header trust.
The stdlib test server is not a hardened public deployment/DoS boundary.
"""
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import sqlite3
import ssl
from .market_data import JST
from .webhook_pipeline import Inbox

IDENTITY={'countryName':'US','stateOrProvinceName':'Ohio','localityName':'Westerville',
          'organizationName':'TradingView, Inc.','commonName':'webhook-server@tradingview.com'}


def valid_identity(cert):
    if not isinstance(cert,dict):return False
    values={}
    for group in cert.get('subject',()):
        for key,value in group:
            if key in values:return False
            values[key]=value
    return (all(values.get(k)==v for k,v in IDENTITY.items())
            and ('email','webhook-server@tradingview.com') in cert.get('subjectAltName',()))


def tls_context(certfile,keyfile,client_ca):
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile,keyfile)
    ctx.load_verify_locations(cafile=client_ca)
    ctx.verify_mode=ssl.CERT_REQUIRED
    return ctx


class Receiver(HTTPServer):
    def __init__(self,address,context,database,*,clock=lambda:datetime.now(JST)):
        if context.verify_mode!=ssl.CERT_REQUIRED:
            raise ValueError('CLIENT_CERTIFICATE_REQUIRED')
        # Private smoke-test server only. Choose and validate real hosting first.
        if address[0]!='127.0.0.1':raise ValueError('LOOPBACK_TEST_SERVER_ONLY')
        self.tls=context;self.database=str(database);self.clock=clock
        Inbox(database).close()
        super().__init__(address,Handler)

    def get_request(self):
        raw,address=super().get_request();raw.settimeout(1.5)
        try:return self.tls.wrap_socket(raw,server_side=True),address
        except Exception:raw.close();raise


class Handler(BaseHTTPRequestHandler):
    server_version='AstraReceiver'
    sys_version=''
    def log_message(self,*args):pass

    def reply(self,status,value):
        data=json.dumps(value,separators=(',',':')).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close');self.end_headers();self.wfile.write(data)

    def do_POST(self):
        if (not isinstance(self.connection,ssl.SSLSocket)
                or self.connection.context.verify_mode!=ssl.CERT_REQUIRED
                or not valid_identity(self.connection.getpeercert())):
            return self.reply(403,{'error':'UNAUTHENTICATED'})
        if self.path!='/tradingview':return self.reply(404,{'error':'NOT_FOUND'})
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
            return self.reply(400,{'error':'INVALID_ENVELOPE'})
        size=int(lengths[0])
        if not 0<size<=4096:return self.reply(413,{'error':'BODY_SIZE'})
        if self.headers.get_content_type()!='application/json':
            return self.reply(415,{'error':'JSON_REQUIRED'})
        inbox=None
        try:
            raw=self.rfile.read(size)
            if len(raw)!=size:raise ValueError('TRUNCATED')
            body=raw.decode('utf-8')
            inbox=Inbox(self.server.database)
            result=inbox.accept(body,now=self.server.clock(),transport_verified=True)
        except (ValueError,TypeError,OverflowError):return self.reply(400,{'error':'INVALID_OBSERVATION'})
        except (sqlite3.Error,OSError):return self.reply(503,{'error':'NOT_PERSISTED'})
        finally:
            if inbox:inbox.close()
        # No Sheet reads, provider calls or order logic in the request path.
        return self.reply(202,result)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('cert','key','client-ca','database'):ap.add_argument('--'+name,required=True)
    ap.add_argument('--port',type=int,default=8443)
    a=ap.parse_args()
    server=Receiver(('127.0.0.1',a.port),tls_context(a.cert,a.key,a.client_ca),a.database)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
