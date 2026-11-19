"""Parse the sandbox runner's JUnit output and sanitize diagnostics before they reach a model."""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET  # input is size-bounded and produced by our own runner

from ..domain.models import TestDetail

MARKER_BEGIN = "<<<SAFELOOP_JUNIT_BEGIN>>>"
MARKER_END = "<<<SAFELOOP_JUNIT_END>>>"

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S+"),
]
_PATH = re.compile(r"/(?:tmp|work|home|app|opt)/[^\s:'\"]*")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_diagnostics(text: str, max_chars: int = 2000) -> str:
    text = _CTRL.sub("", text)
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    text = _PATH.sub("<path>", text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text


class MalformedTestOutput(ValueError):
    pass


def parse_junit(stdout: str) -> tuple[list[TestDetail], int, int]:
    """Return (details, passed, failed). Raises MalformedTestOutput if unusable."""
    b, e = stdout.find(MARKER_BEGIN), stdout.find(MARKER_END)
    if b == -1 or e == -1 or e < b:
        raise MalformedTestOutput("junit markers missing")
    xml = stdout[b + len(MARKER_BEGIN) : e].strip()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise MalformedTestOutput("junit xml unparseable") from exc
    details: list[TestDetail] = []
    for case in root.iter("testcase"):
        node = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        outcome, msg = "passed", ""
        for child in case:
            if child.tag in ("failure", "error", "skipped"):
                outcome = "failed" if child.tag == "failure" else child.tag
                msg = sanitize_diagnostics(child.get("message", "") or (child.text or ""), 500)
                break
        details.append(TestDetail(node_id=node, outcome=outcome, message=msg))
    passed = sum(d.outcome == "passed" for d in details)
    failed = sum(d.outcome in ("failed", "error") for d in details)
    return details, passed, failed
