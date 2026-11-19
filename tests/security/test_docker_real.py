"""REAL isolation tests. They need a Docker daemon and the image:
    docker build -f Dockerfile.sandbox -t safeloop-sandbox:1 .
They are SKIPPED otherwise. They were NOT run in the authoring environment (no Docker)."""
import pytest

from safeloop.core.config import SandboxLimits
from safeloop.domain.enums import FailureCategory
from safeloop.domain.models import CodeArtifact
from safeloop.sandbox.docker_runner import DockerSandbox
from safeloop.verification.test_runner import execute_and_verify
from safeloop.verification.verification_policy import validate_artifact

pytestmark = pytest.mark.docker


def _sandbox(**kw):
    sb = DockerSandbox(SandboxLimits(**kw))
    ok, detail = sb.healthcheck()
    if not ok:
        pytest.skip(f"Docker unavailable: {detail}")
    try:
        sb._docker().images.get(sb.limits.image)
    except Exception:
        pytest.skip(f"sandbox image {sb.limits.image} not built")
    return sb


def _run(sb, code, tests):
    art = CodeArtifact(task_id="sec", attempt_number=1, source_code=code, content_hash="h")
    return execute_and_verify(sb, art, tests, validate_artifact(art))


def _leftovers(sb):
    return sb._docker().containers.list(all=True, filters={"label": "safeloop=1"})


def test_passing_solution_yields_evidence_and_cleans_up():
    sb = _sandbox()
    r = _run(sb, "def add(a, b):\n    return a + b\n", "from solution import add\ndef test_a():\n    assert add(1,2)==3\n")
    assert r.acceptance_met and r.tests_passed == 1
    assert _leftovers(sb) == []


def test_network_is_blocked():
    sb = _sandbox()
    tests = ("import socket, pytest\nfrom solution import f\n\ndef test_net():\n"
             "    with pytest.raises(OSError):\n        socket.create_connection(('1.1.1.1', 53), timeout=2)\n")
    assert _run(sb, "def f():\n    return 1\n", tests).tests_passed == 1


def test_rootfs_is_read_only_and_user_is_non_root():
    sb = _sandbox()
    tests = ("import os, pytest\nfrom solution import f\n\ndef test_ro():\n"
             "    with pytest.raises(OSError):\n        open('/etc/safeloop_probe', 'w')\n\n"
             "def test_uid():\n    assert os.getuid() != 0\n")
    assert _run(sb, "def f():\n    return 1\n", tests).tests_passed == 2


def test_wall_clock_timeout_enforced_and_container_removed():
    sb = _sandbox(wall_clock_seconds=3)
    r = _run(sb, "def f():\n    while True:\n        pass\n", "from solution import f\ndef test_a():\n    f()\n")
    assert r.failure_category == FailureCategory.TEST_TIMEOUT
    assert _leftovers(sb) == []


def test_memory_limit_enforced():
    sb = _sandbox(memory_mb=64)
    r = _run(sb, "def f():\n    return bytearray(512 * 1024 * 1024)\n", "from solution import f\ndef test_a():\n    f()\n")
    assert r.acceptance_met is False


def test_no_host_secrets_in_environment(monkeypatch):
    monkeypatch.setenv("SAFELOOP_API_TOKEN", "super-secret-token")
    sb = _sandbox()
    tests = "import os\nfrom solution import f\n\ndef test_env():\n    assert 'SAFELOOP_API_TOKEN' not in os.environ\n"
    assert _run(sb, "def f():\n    return 1\n", tests).tests_passed == 1
