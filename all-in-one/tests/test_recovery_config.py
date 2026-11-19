import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('recovery_config', Path(__file__).resolve().parents[1] / 'installer/recovery_config.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {key: 'recipient-' + key for key in recovery.BINDINGS}
        self.config.update(mysql_host='10.0.0.10', artifact_bucket='original-bucket',
                           registry_password='recipient-registry', oidc_client_secret='recipient-client',
                           namespace='ma-system', public_hostname='recipient.example.test')
        connections = {key: 'synthetic-' + key for key in recovery.CONNECTIONS}
        connections.update(mysql_host='10.0.0.20', mysql_expected_hostname='mysql-restore.example.test')
        self.profile = {'format': 'michelangelo-recovery-connections-v1',
                        'recipient': {key: self.config[key] for key in recovery.BINDINGS},
                        'connections': connections}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.work.chmod(0o700)
        self.path = self.work / recovery.PROFILE_NAME

    def test_fresh_base_on_later_apply_retains_recovered_connections(self):
        original = copy.deepcopy(self.config)
        first = recovery.compose(self.config, self.profile)
        later = recovery.compose(dict(original, registry_password='rotated-registry'), self.profile)
        self.assertEqual(self.config, original)
        self.assertEqual(first['mysql_host'], later['mysql_host'])
        self.assertEqual(later['artifact_bucket'], self.profile['connections']['artifact_bucket'])
        self.assertEqual(later['registry_password'], 'rotated-registry')
        for key in ('oidc_client_secret', 'namespace', 'public_hostname', *recovery.BINDINGS):
            self.assertEqual(later[key], original[key])

    def test_profile_cannot_transfer_site_authority(self):
        for key in ('cluster_id', 'region', 'oidc_client_secret', 'registry_password', 'namespace'):
            with self.subTest(key=key):
                profile = copy.deepcopy(self.profile)
                profile['connections'][key] = 'foreign-authority'
                with self.assertRaises(RuntimeError):
                    recovery.compose(self.config, profile)
        for key in recovery.BINDINGS:
            with self.subTest(binding=key):
                profile = copy.deepcopy(self.profile)
                profile['recipient'][key] = 'another-site'
                with self.assertRaises(RuntimeError):
                    recovery.compose(self.config, profile)

    def test_invalid_endpoints_and_incomplete_profile_fail_closed(self):
        for address in ('8.8.8.8', '127.0.0.1', '169.254.169.254', '192.0.2.1', '::1', 'db.example.test'):
            with self.subTest(address=address):
                profile = copy.deepcopy(self.profile)
                profile['connections']['mysql_host'] = address
                with self.assertRaises(RuntimeError):
                    recovery.compose(self.config, profile)
        for hostname in ('-db.example.test', 'db..example.test', 'db.example.test\nother', 'a' * 64 + '.test'):
            profile = copy.deepcopy(self.profile)
            profile['connections']['mysql_expected_hostname'] = hostname
            with self.assertRaises(RuntimeError):
                recovery.compose(self.config, profile)
        for key in recovery.CONNECTIONS:
            profile = copy.deepcopy(self.profile)
            del profile['connections'][key]
            with self.assertRaises(RuntimeError):
                recovery.compose(self.config, profile)

    def test_missing_profile_preserves_normal_install(self):
        config, active = recovery.load(self.config, self.work)
        self.assertIs(config, self.config)
        self.assertFalse(active)

    def write_profile(self):
        self.path.write_text(json.dumps(self.profile))
        self.path.chmod(0o600)

    def root_owned(self):
        # CI and developer machines are non-root. Substitute ownership only;
        # real descriptors, modes, links, payload reads and size checks remain.
        real_fstat, real_stat = os.fstat, Path.stat
        def owned(info):
            return types.SimpleNamespace(st_uid=0, st_mode=info.st_mode,
                                         st_nlink=info.st_nlink, st_size=info.st_size)
        return patch.object(recovery.os, 'fstat', lambda fd: owned(real_fstat(fd))), patch.object(Path, 'stat', lambda p: owned(real_stat(p)))

    def test_protected_file_load_and_invalid_content(self):
        self.write_profile()
        owner, parent = self.root_owned()
        with owner, parent:
            result, active = recovery.load(self.config, self.work)
            self.assertTrue(active)
            self.assertEqual(result['mysql_host'], '10.0.0.20')
            self.path.write_text('{invalid')
            with self.assertRaisesRegex(RuntimeError, 'valid JSON'):
                recovery.load(self.config, self.work)
            self.path.write_bytes(b'x' * (recovery.MAX_BYTES + 1))
            with self.assertRaisesRegex(RuntimeError, 'too large'):
                recovery.load(self.config, self.work)

    def test_unsafe_file_forms_are_rejected(self):
        self.write_profile()
        owner, parent = self.root_owned()
        with owner, parent:
            self.path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                recovery.load(self.config, self.work)

            self.path.chmod(0o600)
            self.work.chmod(0o770)
            with self.assertRaisesRegex(RuntimeError, 'directory'):
                recovery.load(self.config, self.work)
            self.work.chmod(0o700)
            linked = self.work / 'linked'
            os.link(self.path, linked)
            with self.assertRaises(RuntimeError):
                recovery.load(self.config, self.work)
            linked.unlink()
            self.path.unlink()
            self.path.symlink_to(self.work / 'missing')
            with self.assertRaisesRegex(RuntimeError, 'safely open'):
                recovery.load(self.config, self.work)

    def test_non_root_profile_owner_is_rejected(self):
        self.write_profile()
        info = os.stat(self.path)
        non_root = types.SimpleNamespace(st_uid=1000, st_mode=info.st_mode,
                                         st_nlink=info.st_nlink, st_size=info.st_size)
        with patch.object(recovery.os, 'fstat', return_value=non_root):
            with self.assertRaisesRegex(RuntimeError, 'root-owned'):
                recovery.load(self.config, self.work)


if __name__ == '__main__':
    unittest.main()
