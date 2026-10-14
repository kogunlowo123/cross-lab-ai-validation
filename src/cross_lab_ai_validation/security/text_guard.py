"""Free-text guard for justification and signature statements.

The platform holds aggregate statistics only. Free-text fields are the one
place a user or agent could paste a patient identifier or a credential, so
they are scanned with the agentic-core PII and secret detectors and rejected
(never silently redacted) when anything is found.
"""

from __future__ import annotations

from agentic_core.security import PiiDetector, SecretScanner

from cross_lab_ai_validation.domain.errors import UnsafeTextError


class TextGuard:
    def __init__(
        self, pii: PiiDetector | None = None, secrets: SecretScanner | None = None
    ) -> None:
        self._pii = pii or PiiDetector()
        self._secrets = secrets or SecretScanner()

    def check(self, field: str, text: str) -> str:
        findings = [*self._pii.detect(text), *self._secrets.detect(text)]
        if findings:
            kinds = sorted({f.kind for f in findings})
            raise UnsafeTextError(
                f"{field} must not contain patient identifiers or credentials (found {kinds})"
            )
        return text
