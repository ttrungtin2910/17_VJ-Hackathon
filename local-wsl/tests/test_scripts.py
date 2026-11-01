"""CLI double: không chạy Docker thật, không gọi provider hoặc xóa volume."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOUBLE = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['DOUBLE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['info']:
    sys.exit(1 if os.environ.get('DAEMON_FAIL') else 0)
if args[:1] == ['ps']:
    sys.exit(0)
if args[:1] == ['inspect']:
    print(json.dumps([{'State': {'Status': 'exited', 'ExitCode': int(os.environ.get('MIGRATION_FAIL', '0'))}}]))
    sys.exit(0)
if args[:1] != ['compose']:
    sys.exit(0)
if args[1:] == ['version']:
    sys.exit(0)
project = args[args.index('--project-name') + 1]
if 'config' in args and '--format' in args:
    ports = [(int(os.environ.get('DOUBLE_PORT', '35173')), 80), (38080, 8000), (39000, 9000)]
    names = ['web', 'api', 'rustfs']
    services = {name: {'ports': [{'host_ip': '127.0.0.1', 'published': str(port), 'target': target}]} for name, (port, target) in zip(names, ports)}
    services['web']['build'] = {'args': {'VITE_DATA_MODE': 'real', 'VITE_API_BASE_URL': 'http://localhost:38080/v1'}}
    services['api']['environment'] = {'OPENAI_API_KEY': os.environ.get('DOUBLE_KEY', ''), 'FPT_VLM_API_KEY': ''}
    services.update({name: {} for name in ['postgres', 'storage-init', 'migrate', 'worker']})
    for name in ['api', 'worker', 'storage-init', 'migrate']:
        services[name].setdefault('environment', {}).update({'DATABASE_URL': 'postgresql://garblo:test@postgres:5432/' + os.environ.get('DOUBLE_DB', 'garblo_test'), 'S3_ENDPOINT_URL': 'http://rustfs:9000', 'S3_BUCKET': 'garblo-test-assets'})
    print(json.dumps({'name': project, 'services': services, 'volumes': {'postgres-data': {'name': project + '_postgres-data'}, 'rustfs-data': {'name': project + '_rustfs-data'}}}))
elif 'build' in args:
    time.sleep(float(os.environ.get('DOUBLE_BUILD_DELAY', '0')))
elif 'ps' in args:
    print('test-container')
elif 'up' in args and os.environ.get('READY_FAIL') and 'api' in args:
    sys.exit(1)
'''


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="garblo scripts ", dir="/tmp/opencode")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.root = self.path / "local wsl"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".runtime", ".env", "__pycache__"))
        binary = self.path / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(DOUBLE)
        docker.chmod(0o700)
        self.log = self.path / "docker.jsonl"
        self.env = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "DOUBLE_LOG": str(self.log),
            "GARBLO_COMPOSE_PROJECT": "garblo-local-wsl-test",
            "GARBLO_ENV_FILE": str(self.root / "tests/env.test"),
            "GARBLO_COMPOSE_OVERRIDE_FILE": str(self.root / "deploy/compose.test.yml"),
            "GARBLO_TEST_MODE": "1",
        }

    def run_script(self, name="start-all.sh", **updates):
        return subprocess.run(
            ["bash", str(self.root / name)], cwd=self.path,
            env={**self.env, **updates}, capture_output=True, text=True, timeout=30,
        )

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_missing_env(self):
        result = self.run_script(GARBLO_ENV_FILE=str(self.path / "missing.env"))
        self.assertNotEqual(result.returncode, 0)

    def test_missing_key(self):
        result = self.run_script(GARBLO_TEST_MODE="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in args for args in self.commands()))

    def test_daemon_error(self):
        self.assertNotEqual(self.run_script(DAEMON_FAIL="1").returncode, 0)

    def test_test_database_cannot_target_runtime(self):
        result = self.run_script(DOUBLE_DB="garblo")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in args for args in self.commands()))

    def test_foreign_port_is_not_killed(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            result = self.run_script(DOUBLE_PORT=str(sock.getsockname()[1]))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("up" in args for args in self.commands()))

    def test_absolute_path_spaces_and_repeated_lifecycle(self):
        for name in ["start-all.sh", "start-all.sh", "stop-all.sh", "stop-all.sh"]:
            result = self.run_script(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        for args in self.commands():
            if args[:1] == ["compose"] and "--project-name" in args:
                self.assertEqual(args[args.index("--project-name") + 1], "garblo-local-wsl-test")
            self.assertNotIn("-v", args)
            self.assertNotIn("--volumes", args)
        self.assertEqual(sum("migrate" in args and "--force-recreate" in args for args in self.commands()), 2)

    def test_stop_before_start_without_key_or_env(self):
        result = self.run_script("stop-all.sh", GARBLO_ENV_FILE=str(self.path / "missing.env"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_testmode_refused_on_runtime_project(self):
        self.assertNotEqual(self.run_script(GARBLO_COMPOSE_PROJECT="garblo-local-wsl").returncode, 0)
        self.assertFalse(self.commands())

    def test_testmode_requires_separate_override(self):
        self.assertNotEqual(self.run_script(GARBLO_COMPOSE_OVERRIDE_FILE="").returncode, 0)

    def test_readiness_timeout_and_migration_failure(self):
        for updates in [{"READY_FAIL": "1"}, {"MIGRATION_FAIL": "1"}]:
            self.assertNotEqual(self.run_script(**updates).returncode, 0)

    def test_concurrent_start_is_serialized(self):
        env = {**self.env, "DOUBLE_BUILD_DELAY": "0.3"}
        first = subprocess.Popen(["bash", str(self.root / "start-all.sh")], cwd=self.path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        second = subprocess.Popen(["bash", str(self.root / "start-all.sh")], cwd=self.path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(first.wait(timeout=30), 0)
        self.assertEqual(second.wait(timeout=30), 0)
        self.assertEqual(sum("migrate" in args and "--force-recreate" in args for args in self.commands()), 2)
        commands = self.commands()
        builds = [i for i, args in enumerate(commands) if "build" in args]
        ready = [i for i, args in enumerate(commands) if "up" in args and "api" in args]
        self.assertLess(ready[0], builds[1])

    def test_dotenv_is_not_executed_and_key_not_printed(self):
        sentinel = self.path / "must not exist"
        (self.root / "tests/env.test").write_text(f'OPENAI_API_KEY=$(touch "{sentinel}")\n')
        result = self.run_script(GARBLO_TEST_MODE="0", DOUBLE_KEY="secret-canary-only-for-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertNotIn("secret-canary-only-for-test", result.stdout + result.stderr)

    def test_live_missing_optin_never_calls_docker(self):
        result = self.run_script("tests/test-live.sh", GARBLO_LIVE_TEST="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.commands())

    def test_live_rejects_controlled_project_before_docker(self):
        result = self.run_script(
            "tests/test-live.sh", GARBLO_LIVE_TEST="1", GARBLO_LIVE_IMAGE_LICENSED="1",
            GARBLO_LIVE_IMAGE_PATH=str(self.path / "synthetic.png"), GARBLO_LIVE_MAX_REQUESTS="1",
            GARBLO_LIVE_MAX_COST_USD="1", GARBLO_LIVE_EMAIL="test@example.com",
            GARBLO_LIVE_PASSWORD="synthetic-test-only",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.commands())


if __name__ == "__main__":
    unittest.main()
