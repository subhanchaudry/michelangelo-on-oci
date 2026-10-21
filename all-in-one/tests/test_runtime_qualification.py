import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'installer'))
import install


class RuntimeQualificationTests(unittest.TestCase):
    def exercise(self, outcome):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root/'ray-runtime-smoke.py').write_text('public qualification source')
        calls = []
        def docker(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == 'run':
                self.assertEqual(kwargs['timeout'], 180)
                self.assertIn('65534:65534', args)
                self.assertEqual(args[args.index('--memory')+1], '4g')
                self.assertEqual(args[args.index('--pids-limit')+1], '2048')
                self.assertEqual(args[args.index('--mount')+1],
                                 'type=bind,src='+str(root/'ray-runtime-smoke.py')+',dst=/qualification.py,readonly')
                if outcome == 'timeout':
                    raise subprocess.TimeoutExpired(args, 180, output=b'agent did not register')
                marker = json.dumps({'ray_startup':'passed','remote_task':'passed','uid':65534})
                return subprocess.CompletedProcess(args, 2 if outcome == 'failure' else 0,
                                                   '' if outcome == 'missing-marker' else marker+'\n', 'agent error' if outcome == 'failure' else '')
            if args[1] == 'inspect':
                return subprocess.CompletedProcess(args, 0, json.dumps([{'State':{'Running':False},'Image':'sha256:example'}]), '')
            self.assertEqual(args[1:3], ['rm','-f'])
            if outcome == 'cleanup-timeout':
                raise subprocess.TimeoutExpired(args, 30)
            return subprocess.CompletedProcess(args, 0, 'removed', '')
        with patch.object(install, 'ROOT', root), patch.object(install, 'WORK', root/'evidence'), patch.object(install.subprocess, 'run', side_effect=docker):
            if outcome == 'success':
                install.qualify_ray_runtime('registry.example.invalid/test@sha256:'+'a'*64)
            else:
                with self.assertRaises(RuntimeError):
                    install.qualify_ray_runtime('registry.example.invalid/test@sha256:'+'a'*64)
        report = json.loads((root/'evidence/ray-qualification.json').read_text())
        self.assertEqual(report['container_removed'], outcome != 'cleanup-timeout')
        self.assertEqual(calls[0][0][calls[0][0].index('--name')+1], calls[-1][0][-1])
        return report, (root/'evidence/ray-qualification.log').read_text()

    def test_success_requires_real_remote_task_marker(self):
        report, _ = self.exercise('success')
        self.assertEqual(report['status'], 'passed')

    def test_timeout_preserves_partial_log_and_removes_container(self):
        report, log = self.exercise('timeout')
        self.assertEqual(report['status'], 'timed-out')
        self.assertIn('agent did not register', log)

    def test_runtime_failure_removes_container_and_keeps_gate_closed(self):
        report, log = self.exercise('failure')
        self.assertEqual(report['status'], 'failed')
        self.assertIn('agent error', log)

    def test_cleanup_timeout_is_reported_and_keeps_gate_closed(self):
        report, _ = self.exercise('cleanup-timeout')
        self.assertEqual(report['status'], 'cleanup-failed')

    def test_zero_exit_without_task_result_keeps_gate_closed(self):
        report, _ = self.exercise('missing-marker')
        self.assertEqual(report['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
