"""Docker-backed sandbox.

STATUS: written against the docker SDK API but NOT verified against a live daemon in the
authoring environment (no Docker available). Run `pytest -m docker` on a Docker host.

Hardening: non-root user, no network, all capabilities dropped, no-new-privileges, read-only
root FS, bounded tmpfs, CPU/memory/PID limits, wall-clock timeout, bounded output, no host
mounts, no secrets in the environment, one container per execution. Input files are streamed
over stdin so no host directory is mounted. The Docker socket is never exposed to the container.

Residual risk: containers share the host kernel. For hostile multi-tenant execution use VMs
or a hardened runtime (e.g. gVisor, Kata, Firecracker).
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Any

from ..core.config import SandboxLimits
from ..domain.exceptions import SandboxExecutionError, SandboxUnavailable
from .base import RawExecution
from .sandbox_policy import validate_files, validate_limits


class DockerSandbox:
    def __init__(self, limits: SandboxLimits | None = None, client: Any | None = None):
        self.limits = limits or SandboxLimits()
        validate_limits(self.limits)  # raises PolicyViolation on unsafe config
        self._client = client
        self._state: dict[str, dict[str, Any]] = {}

    def _docker(self) -> Any:
        if self._client is None:
            try:
                import docker  # optional dependency
            except ImportError as exc:
                raise SandboxUnavailable("docker SDK not installed") from exc
            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:  # fail closed on ANY daemon problem
                self._client = None
                raise SandboxUnavailable(f"docker daemon unavailable: {type(exc).__name__}") from exc
        return self._client

    def healthcheck(self) -> tuple[bool, str]:
        try:
            self._docker()
            return True, "docker daemon reachable"
        except SandboxUnavailable as exc:
            return False, str(exc)

    def create_execution(self, task_id: str, files: dict[str, str]) -> str:
        validate_files(files)
        client = self._docker()
        lim = self.limits
        name = f"safeloop-{uuid.uuid4().hex[:12]}"
        try:
            container = client.containers.create(
                lim.image,
                name=name,
                user="10001:10001",
                network_disabled=True,
                network_mode="none",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                read_only=True,
                tmpfs={"/tmp": f"rw,noexec,nosuid,size={lim.tmpfs_mb}m"},
                mem_limit=f"{lim.memory_mb}m",
                memswap_limit=f"{lim.memory_mb}m",
                nano_cpus=int(lim.cpu_count * 1e9),
                pids_limit=lim.pids_limit,
                privileged=False,
                environment={},  # never forward application secrets
                labels={"safeloop": "1", "safeloop.task": task_id},
                stdin_open=True,
                detach=True,
            )
        except Exception as exc:
            raise SandboxUnavailable(f"cannot create hardened container: {type(exc).__name__}") from exc
        self._state[container.id] = {"c": container, "payload": json.dumps({"files": files}).encode()}
        return container.id

    def execute_tests(self, execution_id: str) -> None:
        st = self._state[execution_id]
        c = st["c"]
        st["start"] = time.monotonic()
        try:
            c.start()
            sock = c.attach_socket(params={"stdin": 1, "stream": 1})
            raw = getattr(sock, "_sock", sock)
            raw.sendall(st["payload"])
            raw.shutdown(socket.SHUT_WR)
            sock.close()
        except Exception as exc:
            raise SandboxExecutionError(f"failed to start execution: {type(exc).__name__}") from exc

    def collect_results(self, execution_id: str) -> RawExecution:
        st = self._state[execution_id]
        c = st["c"]
        lim = self.limits
        timed_out = False
        result: dict[str, Any] = {}

        def _wait() -> None:
            try:
                result.update(c.wait())
            except Exception:  # connection errors are surfaced as missing result
                pass

        t = threading.Thread(target=_wait, daemon=True)
        t.start()
        t.join(lim.wall_clock_seconds)
        if t.is_alive():
            timed_out = True
            self.terminate_execution(execution_id)
            t.join(5)
        duration = time.monotonic() - st.get("start", time.monotonic())
        raw_out = c.logs(stdout=True, stderr=False)
        raw_err = c.logs(stdout=False, stderr=True)
        truncated = len(raw_out) > lim.max_output_bytes or len(raw_err) > lim.max_output_bytes
        out = raw_out[-lim.max_output_bytes :].decode("utf-8", "replace")
        err = raw_err[-lim.max_output_bytes :].decode("utf-8", "replace")
        oom = False
        try:
            c.reload()
            oom = bool(c.attrs.get("State", {}).get("OOMKilled", False))
        except Exception:
            pass
        return RawExecution(
            exit_code=None if timed_out else result.get("StatusCode"),
            stdout=out, stderr=err, duration=duration,
            timed_out=timed_out, oom_killed=oom, truncated=truncated,
        )

    def terminate_execution(self, execution_id: str) -> None:
        st = self._state.get(execution_id)
        if st:
            try:
                st["c"].kill()
            except Exception:
                pass  # already stopped

    def cleanup_execution(self, execution_id: str) -> None:
        st = self._state.pop(execution_id, None)
        if st:
            try:
                st["c"].remove(force=True, v=True)
            except Exception as exc:
                raise SandboxExecutionError(f"cleanup failed: {type(exc).__name__}") from exc
