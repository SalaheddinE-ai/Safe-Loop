import pytest

from safeloop.core.config import SandboxLimits
from safeloop.domain.exceptions import PolicyViolation, SandboxUnavailable
from safeloop.sandbox.docker_runner import DockerSandbox
from safeloop.sandbox.sandbox_policy import validate_files


def test_network_cannot_be_enabled():
    with pytest.raises(PolicyViolation):
        DockerSandbox(SandboxLimits(network_enabled=True))


@pytest.mark.parametrize("name", ["../x.py", "a/b.py", "x.sh", "", "a b.py", "/etc/passwd"])
def test_unsafe_filenames_rejected(name):
    with pytest.raises(PolicyViolation):
        validate_files({name: "x"})


def test_oversized_file_rejected():
    with pytest.raises(PolicyViolation):
        validate_files({"solution.py": "x" * (200 * 1024)})


def test_fails_closed_when_docker_unavailable():
    class Broken:
        def ping(self):
            raise ConnectionError("no daemon")
    # Inject a client whose ping fails via the lazy path
    sb = DockerSandbox()
    sb._client = None
    import safeloop.sandbox.docker_runner as mod
    class FakeDockerModule:
        @staticmethod
        def from_env():
            return Broken()
    import sys
    sys.modules["docker"] = FakeDockerModule  # type: ignore[assignment]
    try:
        with pytest.raises(SandboxUnavailable):
            sb.create_execution("t", {"solution.py": "x=1\n"})
    finally:
        sys.modules.pop("docker", None)
    assert mod  # silence linter


def test_container_hardening_flags_requested():
    """Checks the *requested* configuration against a recording client (not real isolation)."""
    captured = {}

    class C:
        id = "cid"
    class Containers:
        def create(self, image, **kw):
            captured.update(kw); return C()
    class Client:
        containers = Containers()
        def ping(self): return True
    sb = DockerSandbox(client=Client())
    sb.create_execution("task1", {"solution.py": "x=1\n"})
    assert captured["network_mode"] == "none" and captured["network_disabled"] is True
    assert captured["cap_drop"] == ["ALL"] and captured["read_only"] is True
    assert "no-new-privileges:true" in captured["security_opt"]
    assert captured["privileged"] is False and captured["environment"] == {}
    assert captured["user"] != "0" and "volumes" not in captured and "binds" not in captured
