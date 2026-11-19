import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer/release/platform/serving/lifecycle'))
import delivery


class Store:
    def __init__(self):
        self.reads = []

    def get_object(self, **kwargs):
        self.reads.append(kwargs['Key'])
        return {'ContentLength': 4, 'Body': io.BytesIO(b'data')}


class DeliveryRolloutPhaseTests(unittest.TestCase):
    def entry(self, name='candidate', **overrides):
        return {'name': name, 'storage_path': 's3://models/project/' + name + '.tar', 'sha256': 'a' * 64, **overrides}

    def test_canary_only_loads_selected_replica_then_stages_every_replica(self):
        with tempfile.TemporaryDirectory() as directory:
            clients = []
            for pod in ('pod-a', 'pod-b'):
                client = delivery.Delivery(Store(), 'models', 'project', Path(directory) / pod, replica=pod)
                client.request = lambda *args: None
                clients.append(client)
            serving = self.entry('current', phase='serving')
            canary = self.entry(phase='canary', canary_pod='pod-a')
            with patch.object(delivery, 'unpack_model', side_effect=lambda archive, destination, checksum: destination.mkdir()):
                for client in clients:
                    client.reconcile([serving, canary])
                self.assertEqual(set(clients[0].loaded), {'current', 'candidate'})
                self.assertEqual(set(clients[1].loaded), {'current'})
                self.assertEqual(clients[1].storage.reads, ['project/current.tar'])
                staged = {**canary, 'phase': 'staged', 'canary_pod': ''}
                for client in clients:
                    client.reconcile([serving, staged])
                    self.assertEqual(set(client.loaded), {'current', 'candidate'})
                    client.reconcile([serving])
                    self.assertEqual(set(client.loaded), {'current'})

    def test_invalid_phases_and_unbound_canaries_never_download(self):
        client = delivery.Delivery(Store(), 'models', 'project', '/unused', replica='pod-a')
        for changes in ({'phase': 'unknown'}, {'phase': 'canary'}, {'phase': 'canary', 'canary_pod': '../other'},
                        {'phase': 'canary', 'canary_pod': 'pod-b', 'sha256': ''}):
            with self.subTest(changes=changes), self.assertRaises(delivery.PackageError):
                client.reconcile([self.entry(**changes)])
        self.assertEqual(client.storage.reads, [])
        self.assertIn('candidate', client.validate([self.entry()]))
