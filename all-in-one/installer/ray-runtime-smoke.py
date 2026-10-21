"""Exercise Ray's node and runtime agent as the actual restricted workload UID."""
import json
import os
import signal

assert os.geteuid() == 65534, 'Ray qualification must use the workload identity'
def timeout(signum, frame):
    raise TimeoutError('Ray startup probe deadline expired')

signal.signal(signal.SIGALRM, timeout)
signal.alarm(120)
import ray

try:
    ray.init(num_cpus=1, object_store_memory=128 * 1024 * 1024,
             include_dashboard=True, dashboard_host='127.0.0.1')

    @ray.remote
    def identity():
        import os
        return os.geteuid()

    assert ray.get(identity.remote(), timeout=30) == 65534
    print(json.dumps({'ray_startup': 'passed', 'remote_task': 'passed', 'uid': 65534}), flush=True)
finally:
    ray.shutdown()
