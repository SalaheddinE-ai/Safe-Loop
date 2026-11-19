class SafeLoopError(Exception):
    """Base error."""


class InvalidTaskError(SafeLoopError):
    pass


class PolicyViolation(SafeLoopError):
    def __init__(self, policy: str, detail: str):
        super().__init__(f"{policy}: {detail}")
        self.policy = policy
        self.detail = detail


class ProviderError(SafeLoopError):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class SandboxUnavailable(SafeLoopError):
    """Required isolation could not be established. Never fall back to local execution."""


class SandboxExecutionError(SafeLoopError):
    pass
