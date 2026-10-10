from __future__ import annotations


class ScienceError(ValueError):
    """A typed scientific failure. Failed verification never returns a result."""

    def __init__(self, code: str, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.issues = issues or []

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "issues": self.issues}
