"""Layer 1 (structural) and Layer 2 (AST) validation. AST checks are NOT a security boundary."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..core.fingerprints import sha256_hex
from ..domain.enums import FailureCategory

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class Extraction:
    source: str | None
    failure: FailureCategory = FailureCategory.NONE
    detail: str = ""
    content_hash: str = ""


def extract_code(raw: str, max_bytes: int) -> Extraction:
    if not isinstance(raw, str) or not raw.strip():
        return Extraction(None, FailureCategory.MALFORMED_OUTPUT, "empty model output")
    blocks = _FENCE.findall(raw)
    if len(blocks) > 1:
        return Extraction(None, FailureCategory.MALFORMED_OUTPUT, "multiple code blocks")
    source = (blocks[0] if blocks else raw).strip("\n") + "\n"
    if "```" in source:
        return Extraction(None, FailureCategory.MALFORMED_OUTPUT, "unterminated code fence")
    if len(source.encode("utf-8")) > max_bytes:
        return Extraction(None, FailureCategory.POLICY_VIOLATION, f"artifact exceeds {max_bytes} bytes")
    if "\x00" in source:
        return Extraction(None, FailureCategory.MALFORMED_OUTPUT, "NUL byte in artifact")
    return Extraction(source, content_hash=sha256_hex(source))


def parse_syntax(source: str) -> tuple[ast.Module | None, str]:
    try:
        return ast.parse(source), ""
    except SyntaxError as e:
        return None, f"SyntaxError line {e.lineno}: {e.msg}"
    except (ValueError, RecursionError, MemoryError) as e:  # e.g. null bytes, deep nesting
        return None, f"unparseable source: {type(e).__name__}"
