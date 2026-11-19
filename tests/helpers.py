"""TEST DOUBLES ONLY. ScriptedSandbox does NOT execute code and proves nothing about isolation."""
from safeloop.sandbox.base import RawExecution
from safeloop.verification.result_parser import MARKER_BEGIN, MARKER_END


def junit(cases: list[tuple[str, bool]]) -> str:
    body = "".join(
        f'<testcase classname="test_solution" name="{n}">'
        + ("" if ok else '<failure message="assert failed"/>')
        + "</testcase>"
        for n, ok in cases
    )
    return f"{MARKER_BEGIN}\n<testsuite>{body}</testsuite>\n{MARKER_END}\n"


class ScriptedSandbox:
    """Maps a marker string found in the candidate source to a canned RawExecution."""

    def __init__(self, rules: dict[str, RawExecution], default: RawExecution | None = None):
        self.rules, self.default = rules, default
        self.created, self.cleaned, self.terminated = [], [], []
        self._files: dict[str, dict[str, str]] = {}
        self._n = 0

    def create_execution(self, task_id, files):
        self._n += 1
        eid = f"x{self._n}"
        self._files[eid] = files
        self.created.append(eid)
        return eid

    def execute_tests(self, execution_id): ...

    def collect_results(self, execution_id):
        src = self._files[execution_id]["solution.py"]
        for marker, res in self.rules.items():
            if marker in src:
                return res
        assert self.default is not None, "no rule matched"
        return self.default

    def terminate_execution(self, execution_id):
        self.terminated.append(execution_id)

    def cleanup_execution(self, execution_id):
        self.cleaned.append(execution_id)


def ok_run():
    return RawExecution(0, junit([("test_a", True), ("test_b", True)]), "", 0.2)


def fail_run():
    return RawExecution(1, junit([("test_a", True), ("test_b", False)]), "", 0.2)
