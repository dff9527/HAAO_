from __future__ import annotations

import logging
import os

from orchestrator.redaction import redact_text


class RunnerSecretFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        env_secrets = [
            value
            for key, value in os.environ.items()
            if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET"))
        ]
        self.secrets = [secret for secret in [*(secrets or []), *env_secrets] if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.getMessage()), extra_secrets=self.secrets)
        record.args = ()
        return True


def redacted(value: object, *, secrets: list[str] | None = None) -> str:
    return redact_text(str(value), extra_secrets=[secret for secret in secrets or [] if secret])
