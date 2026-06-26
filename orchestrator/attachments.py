from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from orchestrator.context.untrusted import wrap_untrusted_context
from orchestrator.redaction import redact_text

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CONTEXT_BYTES = 64 * 1024

EXECUTABLE_SUFFIXES = {
    ".app",
    ".bat",
    ".bin",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".dylib",
    ".exe",
    ".msi",
    ".pkg",
    ".scr",
    ".so",
}

EXECUTABLE_MIME_PREFIXES = (
    "application/x-msdownload",
    "application/x-dosexec",
    "application/x-mach-binary",
)

TEXT_LIKE_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".env",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

DOCUMENT_MIME_TYPES = {
    "application/json",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class AttachmentStorageError(ValueError):
    """Raised when an upload cannot be accepted or stored."""


@dataclass(frozen=True)
class StoredUpload:
    id: str
    filename: str
    mime: str
    size: int
    kind: str
    stored_path: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "filename": self.filename,
            "mime": self.mime,
            "size": self.size,
            "kind": self.kind,
            "stored_path": self.stored_path,
        }


class AttachmentStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def store(
        self,
        *,
        project_id: str,
        filename: str,
        mime: str | None,
        content: bytes,
    ) -> StoredUpload:
        cleaned_filename = _safe_filename(filename)
        if not content:
            raise AttachmentStorageError("Attachment cannot be empty")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise AttachmentStorageError(
                f"Attachment exceeds the {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB limit"
            )
        guessed_mime = mime or mimetypes.guess_type(cleaned_filename)[0] or "application/octet-stream"
        suffix = Path(cleaned_filename).suffix.lower()
        if _is_executable(suffix, guessed_mime):
            raise AttachmentStorageError("Executable attachments are not allowed")
        kind = _classify_kind(suffix, guessed_mime)
        if kind is None:
            raise AttachmentStorageError(f"Unsupported attachment type: {guessed_mime}")

        attachment_id = f"ATT-{uuid4().hex}"
        project_dir = self.root / _safe_project_dir(project_id)
        target_dir = project_dir / attachment_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / cleaned_filename
        target.write_bytes(content)
        return StoredUpload(
            id=attachment_id,
            filename=cleaned_filename,
            mime=guessed_mime,
            size=len(content),
            kind=kind,
            stored_path=str(target),
        )


def build_attachment_context(attachment: dict[str, object]) -> dict[str, object]:
    """Return the decomposer-facing attachment payload with text content when safe."""
    payload = dict(attachment)
    if payload.get("type") != "file":
        return payload
    path_value = payload.get("value")
    if not isinstance(path_value, str) or not path_value:
        return payload
    path = Path(path_value)
    if not path.is_file():
        return payload
    mime = str(payload.get("mime") or mimetypes.guess_type(path.name)[0] or "")
    suffix = path.suffix.lower()
    if not _is_text_like(suffix, mime):
        return payload
    raw = path.read_bytes()[: MAX_TEXT_CONTEXT_BYTES + 1]
    truncated = len(raw) > MAX_TEXT_CONTEXT_BYTES
    raw = raw[:MAX_TEXT_CONTEXT_BYTES]
    payload["content"] = wrap_untrusted_context(
        label=str(payload.get("value") or path.name),
        content=redact_text(raw.decode("utf-8", errors="replace")),
    )
    payload["content_truncated"] = truncated
    return payload


def _safe_filename(filename: str) -> str:
    name = Path(filename or "attachment").name.strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name[:180] or "attachment"


def _safe_project_dir(project_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", project_id or "default")[:120] or "default"


def _is_executable(suffix: str, mime: str) -> bool:
    lowered = mime.lower()
    return suffix in EXECUTABLE_SUFFIXES or lowered.startswith(EXECUTABLE_MIME_PREFIXES)


def _classify_kind(suffix: str, mime: str) -> str | None:
    lowered = mime.lower()
    if lowered.startswith("image/"):
        return "image"
    if _is_text_like(suffix, lowered) or lowered in DOCUMENT_MIME_TYPES:
        return "file"
    return None


def _is_text_like(suffix: str, mime: str) -> bool:
    lowered = mime.lower()
    return lowered.startswith("text/") or suffix in TEXT_LIKE_SUFFIXES
