import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('browser_auth', Path(__file__).resolve().parents[1]/'installer/auth.py')
auth = importlib.util.module_from_spec(spec); spec.loader.exec_module(auth)

class BrowserSessionTests(unittest.TestCase):
    def test_refresh_store_is_locked_private_ephemeral_and_bounded(self):
        config = {'public_hostname':'studio.example.com','oidc_issuer':'https://identity.example.com',
                  'oidc_client_id':'test','oidc_client_secret':'test-only',
                  'authorized_emails':['test@example.com'],'acme_email':'test@example.com',
                  'reserved_public_ip':'192.0.2.1','lb_subnet_id':'ocid1.subnet.test'}
        settings = auth.oauth_config(config)
        self.assertEqual(settings['cookie_expire'], '20m')
        self.assertEqual(settings['cookie_refresh'], '0')
        self.assertNotIn('offline_access', settings['scope'].split())
        self.assertEqual(settings['session_store_type'], 'redis')
        self.assertEqual(settings['redis_connection_url'], 'redis://127.0.0.1:6379/0')
        self.assertTrue(settings['cookie_secure']); self.assertTrue(settings['cookie_httponly'])
        self.assertFalse(settings['insecure_oidc_skip_nonce'])
        resources = auth.resources(config)
        deployment = next(r for r in resources if r['kind']=='Deployment')
        pod = deployment['spec']['template']['spec']
        store = next(c for c in pod['containers'] if c['name']=='session-store')
        args = dict(zip(store['args'][::2], store['args'][1::2]))
        self.assertEqual(args['--bind'], '127.0.0.1')
        self.assertEqual(args['--save'], ''); self.assertEqual(args['--appendonly'], 'no')
        self.assertEqual(args['--maxmemory-policy'], 'noeviction')
        self.assertTrue(store['securityContext']['readOnlyRootFilesystem'])
        self.assertFalse(store['securityContext']['allowPrivilegeEscalation'])
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertNotIn('volumeMounts',store)
        self.assertEqual(store['resources']['limits']['memory'],'128Mi')
        self.assertEqual(deployment['spec']['replicas'], 1)
        service = next(r for r in resources if r['kind']=='Service')
        self.assertEqual({p['port'] for p in service['spec']['ports']}, {80,443})
        self.assertEqual(len([r for r in resources if r['kind']=='PersistentVolumeClaim']),1)

if __name__=='__main__':unittest.main()
