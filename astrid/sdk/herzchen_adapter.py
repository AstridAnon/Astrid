"""Optional shared Herzchen identity binding for Astrid's Runtime consumer.

Astrid does not own a store here.  This helper uses Herzchen's neutral
``ResourceRef`` only to preserve the canonical Runtime project identity at
the product adapter boundary; task admission and settlement still go through
the generated Runtime client.
"""

from __future__ import annotations

from typing import Any


class SharedIdentityUnavailable(RuntimeError):
    """The optional shared contract package is unavailable in this install."""


try:
    from herzchen.contracts import ResourceRef
except ModuleNotFoundError as exc:  # pragma: no cover - standalone Astrid install
    ResourceRef = None  # type: ignore[assignment]
    _IMPORT_ERROR: BaseException | None = exc
else:
    _IMPORT_ERROR = None


def runtime_project_ref(project_id: str) -> Any:
    """Return a typed shared ref while retaining the exact Runtime ID."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id must be a non-empty string")
    if ResourceRef is None:
        raise SharedIdentityUnavailable(
            "the pinned Herzchen contract package is required for shared identity binding"
        ) from _IMPORT_ERROR
    return ResourceRef("astrid-runtime", "runtime.project", project_id)


def preserve_runtime_project_id(project_id: str | None) -> str | None:
    """Validate through the shared ref and return the unchanged wire ID."""
    if project_id is None:
        return None
    # Astrid remains installable as a standalone product.  The Runtime
    # generated client remains the compatibility path when the shared package
    # is not present; a pinned controller install activates this validation.
    if ResourceRef is None:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id must be a non-empty string")
        return project_id
    return runtime_project_ref(project_id).id
