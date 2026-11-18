"""Real local DNS/transport regression for the generated SDK GetModel call."""
import ast
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
import threading
import time
import types
import unittest

# Keep the test resolver local and deterministic; never change system DNS.
os.environ['GRPC_DNS_RESOLVER'] = 'ares'
import grpc
import service_auth


class ConnectionWaitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        source = Path(self.temporary.name)
        reviewed = Path(os.environ['MA_REVIEW_SOURCE_DIR'])
        relative = Path('python/michelangelo/api/v2/services')
        shutil.copytree(reviewed / relative, source / relative)
        service_auth.prepare_source(source)
        tree = ast.parse((source / relative / 'gen/model.py').read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'get_model')
        class Request:
            def __init__(self, name, namespace):
                self.name, self.namespace = name, namespace
                self.get_options = types.SimpleNamespace(CopyFrom=lambda value: None)
        namespace = {'GetModelRequest': Request, 'GetOptions': object, '_TIMEOUT_SECONDS': 60}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'generated-get-model', 'exec'), namespace)
        self.get_model = namespace['get_model']
        self.calls = []
        def handle(request, context):
            self.calls.append(request)
            if ('authorization', 'Bearer synthetic-local-identity') not in context.invocation_metadata():
                context.abort(grpc.StatusCode.UNAUTHENTICATED, 'missing test identity')
            if request == b'denied':
                context.abort(grpc.StatusCode.PERMISSION_DENIED, 'denied')
            if request == b'unavailable':
                context.abort(grpc.StatusCode.UNAVAILABLE, 'accepted but unavailable')
            return request
        self.server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=2))
        self.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
            'proof.Service', {'Call': grpc.unary_unary_rpc_method_handler(handle)}),))
        self.port = self.server.add_insecure_port('127.0.0.1:0')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.channel = None
        self.stop_dns = threading.Event()
        self.dns_ready = threading.Event()
        self.dns_queries = []
        self.dns = None
        self.dns_thread = None

    def tearDown(self):
        if self.channel:
            self.channel.close()
        self.server.stop(0).wait(2)
        self.pool.shutdown(wait=True)
        self.stop_dns.set()
        if self.dns_thread:
            self.dns_thread.join(2)
        if self.dns:
            self.dns.close()
        self.temporary.cleanup()

    def client(self, target):
        self.channel = grpc.insecure_channel(target, options=[
            ('grpc.dns_min_time_between_resolutions_ms', 100),
            ('grpc.initial_reconnect_backoff_ms', 100),
            ('grpc.min_reconnect_backoff_ms', 100)])
        stub = types.SimpleNamespace(GetModel=self.channel.unary_unary(
            '/proof.Service/Call', request_serializer=lambda req: req.name.encode(),
            response_deserializer=lambda raw: types.SimpleNamespace(model=raw)))
        return types.SimpleNamespace(_stub=stub,
            _get_metadata=lambda headers: [('authorization', 'Bearer synthetic-local-identity')],
            _process_message_or_dict=lambda *args: None)

    def test_transient_connection_preserves_identity_and_delivers_once(self):
        client = self.client(f'127.0.0.1:{self.port}')
        with self.assertRaises(grpc.FutureTimeoutError):
            grpc.channel_ready_future(self.channel).result(timeout=.15)
        result = self.pool.submit(self.get_model, client, 'synthetic', 'once', timeout=5)
        self.server.start()
        self.assertEqual(result.result(timeout=6), b'once')
        self.assertEqual(self.calls, [b'once'])

    def test_caller_deadline_bounds_connection_wait(self):
        client = self.client(f'127.0.0.1:{self.port}')
        with self.assertRaises(grpc.RpcError) as caught:
            self.get_model(client, 'synthetic', 'never', timeout=.3)
        self.assertEqual(caught.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        self.assertEqual(self.calls, [])

    def test_server_failures_are_not_replayed(self):
        self.server.start()
        client = self.client(f'127.0.0.1:{self.port}')
        for name, code in [('denied', grpc.StatusCode.PERMISSION_DENIED),
                           ('unavailable', grpc.StatusCode.UNAVAILABLE)]:
            with self.assertRaises(grpc.RpcError) as caught:
                self.get_model(client, 'synthetic', name, timeout=2)
            self.assertEqual(caught.exception.code(), code)
        self.assertEqual(self.calls, [b'denied', b'unavailable'])

    def test_actual_dns_failure_then_recovery(self):
        self.dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dns.bind(('127.0.0.1', 0))
        self.dns.settimeout(.1)
        def respond():
            while not self.stop_dns.is_set():
                try:
                    data, peer = self.dns.recvfrom(4096)
                except socket.timeout:
                    continue
                pos = 12
                while data[pos]:
                    pos += 1 + data[pos]
                pos += 1
                kind, _ = struct.unpack('!HH', data[pos:pos+4])
                question = data[12:pos+4]
                ready = self.dns_ready.is_set()
                self.dns_queries.append(ready)
                answer, count = b'', 0
                if ready and kind == 1:
                    answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 0, 4) + socket.inet_aton('127.0.0.1')
                    count = 1
                flags = 0x8180 if ready else 0x8182  # NOERROR or SERVFAIL.
                self.dns.sendto(data[:2] + struct.pack('!HHHHH', flags, 1, count, 0, 0) + question + answer, peer)
        self.dns_thread = threading.Thread(target=respond, daemon=True)
        self.dns_thread.start()
        self.server.start()
        client = self.client(f'dns://127.0.0.1:{self.dns.getsockname()[1]}/rpc-wait.invalid:{self.port}')
        with self.assertRaises(grpc.FutureTimeoutError):
            grpc.channel_ready_future(self.channel).result(timeout=.4)
        self.assertTrue(self.dns_queries)
        result = self.pool.submit(self.get_model, client, 'synthetic', 'once', timeout=8)
        time.sleep(.2)
        self.assertFalse(result.done(), 'RPC must remain queued during the DNS failure')
        self.dns_ready.set()
        self.assertEqual(result.result(timeout=9), b'once')
        self.assertIn(False, self.dns_queries)
        self.assertIn(True, self.dns_queries)
        self.assertEqual(self.calls, [b'once'])


if __name__ == '__main__':
    unittest.main()
