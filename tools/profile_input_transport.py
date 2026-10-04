"""Measure Python dispatch overhead only; no devices, windows or input events."""
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from infrastructure.automation_transport import AutomationTransport


class CounterBackend:
    def __init__(self):
        self.count = 0

    def move(self, x, y):
        self.count += 1


def measure(function, count=100_000):
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        for index in range(count):
            function(index, 10)
        samples.append((time.perf_counter() - start) * 1e6 / count)
    return statistics.median(samples)


backend = CounterBackend()
transport = AutomationTransport(lambda: False, backend)
direct = measure(backend.move)
mediated = measure(transport.move)
assert backend.count == 1_000_000
print(json.dumps(dict(direct_us=direct, transport_us=mediated,
                      dispatch_overhead_us=mediated-direct,
                      calls_per_sample=100_000, samples=5,
                      native_input=False), indent=2))
