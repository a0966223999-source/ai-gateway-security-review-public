"""Original host MAC/signature/fence with explicit loopback response fixtures."""
import base64
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fresh_batch_host import FreshBatchHost,initialize,canonical,digest
from platform_checkpoint_forward import forward_checkpoint,ENDPOINT,MODE

class Response:
    url=ENDPOINT
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read(self,limit):return canonical({'scope':'platform-checkpoint-only','native_work_authority':False,'fixture':'result'})

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)/'fixture-host'
        initialize(root,owner='oauth:'+'a'*64,platform_service=True,enable_codex=True)
        self.host=FreshBatchHost(root);self.requests=[];self.error=None
        self.n=0
        class Opener:
            def open(_,request,timeout):
                self.requests.append(request)
                if self.error:raise self.error
                return Response()
        self.p=patch('platform_checkpoint_forward.build_opener',lambda *a:Opener());self.p.start();self.addCleanup(self.p.stop)

    def envelope(self):
        self.n+=1;c=self.host.config
        body={'generation':c['generation'],'owner':c['owner'],'parent_id':c['parent_id'],
            'action':'read','operation_key':'fixture-op-'+str(self.n),'payload':{'execution_mode':MODE,'checkpoint_action':'link_status'},'expires_at':time.time()+30}
        body['request_id']=digest([c['generation'],c['owner'],body['action'],body['operation_key']])
        return {'body':body,'mac':hmac.new(self.host.ingress,canonical(body),'sha256').hexdigest()}

    def test_preserves_exact_mac_envelope_and_uses_fixed_loopback_without_cookie(self):
        e=self.envelope();r=forward_checkpoint(self.host,e)
        self.assertEqual(self.requests[0].full_url,ENDPOINT)
        self.assertEqual(self.requests[0].data,canonical(e))
        self.assertIsNone(self.requests[0].get_header('Cookie'))
        self.assertIsNone(self.requests[0].get_header('Authorization'))
        self.host.key.public_key().verify(base64.b64decode(r['signature']),canonical(r['body']))
        self.assertFalse(r['body']['native_execution_verified'])
        self.assertEqual(r['body']['result']['kind'],MODE)

    def test_duplicate_is_query_only_without_second_http(self):
        e=self.envelope();forward_checkpoint(self.host,e)
        with self.assertRaises(Exception):forward_checkpoint(self.host,e)
        self.assertEqual(len(self.requests),1)

    def test_forged_mac_never_forwards(self):
        e=self.envelope();e['mac']='0'*64
        with self.assertRaises(Exception):forward_checkpoint(self.host,e)
        self.assertEqual(self.requests,[])

    def test_denial_is_preserved_without_response_body_leak_or_retry(self):
        self.error=HTTPError(ENDPOINT,403,'fixture private detail',None,None)
        r=forward_checkpoint(self.host,self.envelope())
        self.assertEqual(r['body']['result']['http_status'],403)
        self.assertNotIn('private detail',json.dumps(r))
        self.assertEqual(len(self.requests),1)

    def test_timeout_is_unknown_and_attempt_is_retained(self):
        e=self.envelope();self.error=TimeoutError('fixture-private')
        r=forward_checkpoint(self.host,e)
        self.assertEqual(r['body']['result']['status'],'CHECKPOINT_OUTCOME_UNKNOWN_QUERY_ONLY')
        with self.assertRaises(Exception):forward_checkpoint(self.host,e)
        self.assertEqual(len(self.requests),1)
