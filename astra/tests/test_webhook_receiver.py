"""Real loopback TLS tests with disposable LOCAL certificates, not TV calls."""
import http.client
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from astra.webhook_receiver import Receiver,tls_context
from astra.tests.test_webhook_pipeline import fixture,payload


class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        def openssl(*args):subprocess.run(['openssl',*args],cwd=cls.root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        openssl('req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','2',
                '-subj','/CN=Astra Disposable Test CA','-keyout','ca.key','-out','ca.crt')
        for name,subject,extension in [
            ('server','/CN=localhost','subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth'),
            ('valid','/C=US/ST=Ohio/L=Westerville/O=TradingView, Inc./CN=webhook-server@tradingview.com',
             'subjectAltName=email:webhook-server@tradingview.com\nextendedKeyUsage=clientAuth'),
            ('wrong','/CN=unrelated-client','subjectAltName=email:other@example.invalid\nextendedKeyUsage=clientAuth')]:
            openssl('req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-subj',subject,
                    '-keyout',name+'.key','-out',name+'.csr')
            (cls.root/(name+'.ext')).write_text(extension+'\n')
            openssl('x509','-req','-in',name+'.csr','-CA','ca.crt','-CAkey','ca.key','-CAcreateserial',
                    '-days','2','-extfile',name+'.ext','-out',name+'.crt')
        cls.f=fixture();ctx=tls_context(cls.root/'server.crt',cls.root/'server.key',cls.root/'ca.crt')
        cls.server=Receiver(('127.0.0.1',0),ctx,cls.root/'receiver.sqlite',clock=lambda:cls.f.now)
        cls.thread=threading.Thread(target=cls.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.thread.join();cls.server.server_close();cls.tmp.cleanup()

    def request(self,identity='valid',body=None,headers=None):
        ctx=ssl.create_default_context(cafile=str(self.root/'ca.crt'))
        if identity:ctx.load_cert_chain(str(self.root/(identity+'.crt')),str(self.root/(identity+'.key')))
        c=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,context=ctx,timeout=2)
        try:
            c.request('POST','/tradingview',body or payload(self.f),headers or {'Content-Type':'application/json'})
            r=c.getresponse();return r.status,json.loads(r.read())
        finally:c.close()

    def test_verified_tls_ack_is_persistent_and_fast_locally(self):
        start=time.monotonic();status,body=self.request()
        self.assertEqual(status,202);self.assertTrue(body['accepted']);self.assertFalse(body['notification_sent'])
        self.assertLess(time.monotonic()-start,3)

    def test_missing_certificate_cannot_be_replaced_by_header(self):
        with self.assertRaises((ssl.SSLError,ConnectionError)):
            self.request(None,headers={'Content-Type':'application/json','X-Client-Verify':'SUCCESS'})

    def test_trusted_ca_but_wrong_peer_identity_is_rejected(self):
        status,_=self.request('wrong');self.assertEqual(status,403)

    def test_rejects_non_json_and_oversized_body(self):
        self.assertEqual(self.request(headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request(body='x'*4097)[0],413)

    def test_caller_cannot_claim_rank_or_verified_transport(self):
        p=json.loads(payload(self.f));p['transport_verified']=True
        self.assertEqual(self.request(body=json.dumps(p))[0],400)

    def test_insecure_context_and_public_bind_are_disabled(self):
        with self.assertRaises(ValueError):Receiver(('127.0.0.1',0),ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),self.root/'bad.sqlite')
        ctx=tls_context(self.root/'server.crt',self.root/'server.key',self.root/'ca.crt')
        with self.assertRaises(ValueError):Receiver(('0.0.0.0',0),ctx,self.root/'bad.sqlite')
