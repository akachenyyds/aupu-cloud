#!/usr/bin/env python3
"""Run synthetic regressions with internet/device networking disabled."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import socket
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-only', action='store_true')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    attempts = []

    def no_network(event, event_args):
        if event in ('socket.connect', 'socket.sendto'):
            if event_args[0].family in (socket.AF_INET, socket.AF_INET6):
                attempts.append(event)
                raise RuntimeError('Network disabled by the test runner')
        if event in ('socket.getaddrinfo', 'subprocess.Popen'):
            attempts.append(event)
            raise RuntimeError('Network/process access disabled by the test runner')

    sys.addaudithook(no_network)
    sys.path.insert(0, str(ROOT / 'tests'))
    sys.path.insert(0, str(ROOT))
    names = ['test_client', 'test_mqtt', 'test_schedule_duration', 'test_temperature', 'test_auth']
    if not args.unit_only:
        names.extend(['test_ha', 'test_sms_flow'])
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    versions = {}
    for name in ('homeassistant', 'paho-mqtt', 'icmplib', 'voluptuous'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    report = {
        'suite': 'unit' if args.unit_only else 'unit_and_real_ha_framework',
        'python': sys.version.split()[0],
        'dependencies': versions,
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
        'blocked_network_or_process_attempts': len(attempts),
        'duration_seconds': round(time.monotonic() - started, 3),
        'passed': result.wasSuccessful() and not attempts,
        'physical_device_test': False,
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
