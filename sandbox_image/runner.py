"""Runs INSIDE the sandbox container. Reads {"files": {...}} JSON from stdin, runs pytest."""
import json
import os
import re
import subprocess
import sys

BEGIN, END = "<<<SAFELOOP_JUNIT_BEGIN>>>", "<<<SAFELOOP_JUNIT_END>>>"
NAME = re.compile(r"^[A-Za-z0-9_]+\.py$")
WORK = "/tmp/work"


def main() -> int:
    payload = json.loads(sys.stdin.read())
    os.makedirs(WORK, exist_ok=True)
    for name, content in payload["files"].items():
        if not NAME.match(name):
            print("invalid file name", file=sys.stderr)
            return 3
        with open(os.path.join(WORK, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    xml = os.path.join(WORK, "report.xml")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
         f"--junitxml={xml}", "test_solution.py"],
        cwd=WORK, capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout[-8000:])
    if os.path.exists(xml):
        with open(xml, encoding="utf-8") as fh:
            sys.stdout.write(f"\n{BEGIN}\n{fh.read()}\n{END}\n")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
