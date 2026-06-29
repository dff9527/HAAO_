from __future__ import annotations

import hashlib

from orchestrator.db.sqlite import PromptVersionRepository


def prompt_template_hash(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def prompt_version_id(name: str, template: str) -> str:
    digest = prompt_template_hash(template)
    return f"{name}:sha256:{digest[:12]}"


def record_prompt_version(
    repository: PromptVersionRepository,
    *,
    name: str,
    template: str,
) -> str:
    digest = prompt_template_hash(template)
    version_id = f"{name}:sha256:{digest[:12]}"
    repository.record(prompt_id=version_id, template_hash=digest)
    return version_id
