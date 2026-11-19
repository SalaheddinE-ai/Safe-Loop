import pytest

from safeloop.domain.enums import FailureCategory
from safeloop.domain.models import CodeArtifact
from safeloop.verification.ast_validator import extract_code
from safeloop.verification.result_parser import (
    MalformedTestOutput,
    parse_junit,
    sanitize_diagnostics,
)
from safeloop.verification.verification_policy import validate_artifact
from tests.helpers import junit


def test_extract_code_variants():
    assert extract_code("```python\nx=1\n```", 1000).source == "x=1\n"
    assert extract_code("x = 1", 1000).source == "x = 1\n"
    assert extract_code("", 1000).failure == FailureCategory.MALFORMED_OUTPUT
    assert extract_code("```python\na\n```\n```python\nb\n```", 1000).failure == FailureCategory.MALFORMED_OUTPUT
    assert extract_code("x" * 5000, 100).failure == FailureCategory.POLICY_VIOLATION
    e = extract_code("x=1", 100)
    assert len(e.content_hash) == 64


def _art(src):
    return CodeArtifact(task_id="t", attempt_number=1, source_code=src, content_hash="h")


@pytest.mark.parametrize("src", [
    "import os\n", "from subprocess import run\n", "eval('1')\n", "open('f')\n",
    "().__class__.__bases__\n", "import socket\n",
])
def test_static_policy_blocks(src):
    r = validate_artifact(_art(src))
    assert r.syntax_valid and r.failure_category == FailureCategory.POLICY_VIOLATION


def test_syntax_error_and_clean_code():
    assert validate_artifact(_art("def (:\n")).failure_category == FailureCategory.SYNTAX_ERROR
    ok = validate_artifact(_art("def add(a, b):\n    return a + b\n"))
    assert ok.static_checks_passed and ok.failure_category == FailureCategory.NONE


def test_parse_junit_ok_and_malformed():
    d, p, f = parse_junit(junit([("a", True), ("b", False)]))
    assert (p, f) == (1, 1) and d[1].outcome == "failed"
    for bad in ["no markers", "<<<SAFELOOP_JUNIT_BEGIN>>><oops<<<SAFELOOP_JUNIT_END>>>"]:
        with pytest.raises(MalformedTestOutput):
            parse_junit(bad)


def test_sanitize_redacts_and_truncates():
    s = sanitize_diagnostics("key sk-abcdefghijklmnopqrstuv at /tmp/work/solution.py api_key=XYZ \x00" + "a" * 5000, 200)
    assert "sk-abc" not in s and "XYZ" not in s and "/tmp/work" not in s and "\x00" not in s
    assert len(s) < 260
