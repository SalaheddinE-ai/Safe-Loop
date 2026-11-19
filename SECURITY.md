# Security

## Threat model
Assets: host, Docker daemon, API secrets, other tasks' data. Adversaries: untrusted/hallucinating model output, malicious
task text (prompt injection), malicious tests/diagnostics, a curious API client.
Trust: LLM output, test output, and logs are **untrusted**. The API, policies, and sandbox adapter are trusted.

## Controls
| Control | Implemented | Automatically tested | Manually/real-env tested |
|---|---|---|---|
| Generated code never executes in-process | yes | yes (architecture; workflow uses interface) | no |
| Fail closed when sandbox unavailable (no local fallback) | yes | yes + real server boot (no Docker) | yes (boot) |
| Network enablement rejected by policy | yes | yes | no |
| Container: non-root, no network, cap_drop ALL, no-new-privileges, read-only rootfs, tmpfs, CPU/mem/PID limits, no host mounts, empty env | yes (requested flags) | flags verified via recording client | **real tests written, never run** (`pytest -m docker`) |
| Wall-clock timeout + bounded output + cleanup | yes | scripted | real tests written, never run |
| File-name allow-list, size limits | yes | yes | no |
| AST/static checks (limited filter, **not** a boundary) | yes | yes | n/a |
| Untrusted-data fencing + diagnostics sanitizer (secret/path redaction) | yes | yes | n/a |
| Bearer-token auth (constant-time compare) | yes (optional) | yes | no |
| Per-client rate limiting (token bucket, in-memory), 429 + Retry-After | yes | yes | no |
| Retention purge (default 30 days) | yes | partly (primitive tested) | no |
| Bounded queue / 429, bounded retries, attempt/cost/deadline limits | yes | yes | no |
| Log redaction; prompts and code not logged | yes | yes | no |
| Dev-only scripted responses gated off by default | yes | yes | no |

## Residual risks
- Containers share the host kernel; escape bugs exist. For hostile multi-tenant use dedicated VMs or gVisor/Kata/Firecracker.
- Compose mounts the Docker socket into the **API** container (never into sandboxes): compromising the API ~ host root.
  Prefer running the API on the host, a rootless daemon, or a socket proxy.
- Promtail also mounts the socket read-only (container metadata/log access).
- Base image is pinned by tag, not digest. Static checks are bypassable. Tests supplied by users run inside the sandbox too.
- Single shared token, no per-user authorization, single-process rate limiting only, no TLS (terminate TLS at a reverse proxy).
- Artifact export via `include_code` is protected only by the shared token.

## Not implemented
Per-user authz, TLS, distributed rate limiting, seccomp/AppArmor custom profiles, image signature verification.

Report vulnerabilities privately to the repository owner.
