"""Shared Herzchen work resources over Astrid's Runtime-owned APIs.

This module deliberately keeps the two concerns separate:

* Herzchen owns template/pack resource validation and immutable resource
  identity.
* Runtime owns project documents, task admission, receipts, events, and
  execution state.

No FND store, task ledger, pack checkout, or authoring database is created
here.  Durable effects go through the supplied ``WorkspaceClient``.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


class SharedWorkUnavailable(RuntimeError):
    """The installed shared Herzchen package lacks the required pack APIs."""


class SharedWorkError(ValueError):
    """A shared resource cannot be admitted to Runtime."""


try:  # The Astrid standalone distribution remains importable without FND.
    from herzchen.packs.authoring import ManagedPack, describe_compatibility
    from herzchen.packs.templates import WorkProtocol, WorkTemplate, render_template
except ImportError as exc:  # pragma: no cover - exercised by standalone installs
    ManagedPack = WorkProtocol = WorkTemplate = None  # type: ignore[assignment]
    describe_compatibility = render_template = None  # type: ignore[assignment]
    _IMPORT_ERROR: BaseException | None = exc
else:
    _IMPORT_ERROR = None


def _require_shared() -> None:
    if _IMPORT_ERROR is not None:
        raise SharedWorkUnavailable(
            "the pinned Herzchen package with work-template and managed-pack APIs is required"
        ) from _IMPORT_ERROR


def _data(value: Any) -> Mapping[str, Any]:
    """Unwrap generated-client envelopes without narrowing Runtime data."""
    if isinstance(value, Mapping) and isinstance(value.get("data"), Mapping):
        return value["data"]
    if isinstance(value, Mapping):
        return value
    raise SharedWorkError("Runtime returned a non-object resource")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _task_nodes(seed: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Select task nodes from the shared seed without reimplementing expansion."""
    tasks = seed.get("tasks")
    if isinstance(tasks, list):
        return tuple(item for item in tasks if isinstance(item, Mapping))
    work = seed.get("work")
    if isinstance(work, list):
        return tuple(
            item for item in work
            if isinstance(item, Mapping) and str(item.get("kind", "")).removeprefix("work.") == "task"
        )
    return ()


def _local_id(node: Mapping[str, Any], index: int) -> str:
    value = node.get("local_id", node.get("key", node.get("name", f"task-{index}")))
    if not isinstance(value, str) or not value.strip() or "/" in value or "\\" in value:
        raise SharedWorkError("template task local_id must be an opaque non-blank string")
    return value


@dataclass(frozen=True)
class TemplateAdmission:
    """The Runtime receipts for tasks expanded from one shared template."""

    project_id: str
    template_ref: Mapping[str, Any]
    rendered: Any
    tasks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PackRevision:
    """A Runtime document revision containing managed-pack authoring state."""

    project_id: str
    document_id: str
    version: int
    revision: str
    status: str
    response: Mapping[str, Any] | None = None


class RuntimeSharedWorkAdapter:
    """Use shared Herzchen resources while Runtime remains the only writer."""

    def __init__(self, transport: Any) -> None:
        if transport is None:
            raise TypeError("transport is required")
        self.transport = transport
        _require_shared()

    @staticmethod
    def render(template: Any, parameters: Mapping[str, Any] | None = None) -> Any:
        _require_shared()
        if not isinstance(template, WorkTemplate):
            raise SharedWorkError("template must be the shared Herzchen WorkTemplate type")
        return render_template(template, parameters)

    def instantiate_tasks(
        self,
        template: Any,
        parameters: Mapping[str, Any] | None = None,
        *,
        project_id: str,
        logical_request_key: str,
    ) -> TemplateAdmission:
        """Expand shared task resources into existing Runtime project identity."""
        _require_shared()
        if not isinstance(project_id, str) or not project_id.strip():
            raise SharedWorkError("project_id is required")
        if not isinstance(logical_request_key, str) or not logical_request_key.strip():
            raise SharedWorkError("logical_request_key is required")
        project = _data(self.transport.get_project(project_id))
        canonical_project_id = str(project.get("project_id", project.get("id", "")))
        if canonical_project_id != project_id:
            raise SharedWorkError("Runtime returned a different project identity")
        rendered = self.render(template, parameters)
        nodes = _task_nodes(rendered.seed)
        if not nodes:
            raise SharedWorkError("shared template contains no task nodes")

        admissions: list[Mapping[str, Any]] = []
        template_ref = rendered.template.ref.to_dict()
        for index, node in enumerate(nodes):
            local_id = _local_id(node, index)
            fields = dict(node.get("fields") or {})
            capability = node.get("capability_id", fields.pop("capability_id", None))
            if not isinstance(capability, str) or not capability:
                raise SharedWorkError(f"task {local_id!r} does not declare a Runtime capability_id")
            input_ids = node.get("input_object_ids", fields.pop("input_object_ids", []))
            if not isinstance(input_ids, list) or any(not isinstance(item, str) for item in input_ids):
                raise SharedWorkError(f"task {local_id!r} input_object_ids must be a list of strings")
            spec = dict(node.get("spec") or fields.pop("spec", {}) or {})
            spec["template_origin"] = template_ref
            spec["template_local_id"] = local_id
            spec["template_parameters"] = dict(rendered.parameters)
            digest = node.get("capability_digest", fields.pop("capability_digest", None))
            if digest is None:
                digest = spec.get("capability_digest")
            if not isinstance(digest, str) or not digest:
                raise SharedWorkError(f"task {local_id!r} must declare the admitted capability digest")
            effect = node.get("settlement_effect", fields.pop("settlement_effect", None))
            storage = node.get("storage_estimate", fields.pop("storage_estimate", None))
            generation_intent = node.get("generation_intent", fields.pop("generation_intent", None))
            response = self.transport.admit_task(
                capability_id=capability,
                capability_digest=digest,
                input_object_ids=list(input_ids),
                idempotency_key=f"{logical_request_key}:{local_id}",
                project_id=project_id,
                spec=spec,
                settlement_effect=effect,
                generation_intent=generation_intent,
                storage_estimate=storage,
            )
            admission = _data(response)
            # Runtime task admission returns the task/run pair at the
            # service boundary.  The shared adapter exposes the task
            # resource while retaining the Runtime receipt inside the
            # original command response and ledger.
            if isinstance(admission.get("task"), Mapping):
                admission = admission["task"]
            admissions.append(admission)
        return TemplateAdmission(project_id, template_ref, rendered, tuple(admissions))

    def adopt_protocol(
        self,
        project_id: str,
        protocol: Any,
        *,
        logical_request_key: str,
    ) -> Mapping[str, Any]:
        """Attach protocol choice metadata through Runtime's project command."""
        _require_shared()
        if not isinstance(protocol, WorkProtocol):
            raise SharedWorkError("protocol must be the shared Herzchen WorkProtocol type")
        project = _data(self.transport.get_project(project_id))
        metadata = dict(project.get("metadata") or {})
        shared = dict(metadata.get("shared_work") or {})
        shared["protocol_ref"] = protocol.ref.to_dict()
        shared["protocol_definition"] = protocol.to_dict()
        metadata["shared_work"] = shared
        response = self.transport.update_project(
            project_id,
            metadata=metadata,
            expected_version=int(project.get("version", 1)),
            idempotency_key=logical_request_key,
        )
        return _data(response)

    def record_critique(
        self,
        project_id: str,
        task_id: str,
        critique: Mapping[str, Any],
        *,
        logical_request_key: str,
    ) -> Mapping[str, Any]:
        """Persist critique as a separate Runtime document, never task acceptance."""
        if not isinstance(task_id, str) or not task_id.strip() or "/" in task_id or "\\" in task_id:
            raise SharedWorkError("task_id must be an opaque non-blank string")
        if not isinstance(critique, Mapping):
            raise SharedWorkError("critique must be an object")
        response = self.transport.create_document(
            project_id,
            f"creative-critique-{task_id}",
            "creative.critique",
            {
                "record_type": "creative.critique",
                "task_id": task_id,
                "critique": dict(critique),
                "acceptance_authority": "runtime.task",
            },
            idempotency_key=logical_request_key,
        )
        return _data(response)

    @staticmethod
    def pack_document_id(pack_id: str) -> str:
        if not isinstance(pack_id, str) or not pack_id.strip() or "/" in pack_id or "\\" in pack_id:
            raise SharedWorkError("pack_id must be an opaque non-blank string")
        return f"managed-pack-{pack_id}"

    @staticmethod
    def _pack_content(pack: Any, *, revision: str, status: str = "authoring") -> dict[str, Any]:
        _require_shared()
        if not isinstance(pack, ManagedPack):
            raise SharedWorkError("pack must be the shared Herzchen ManagedPack type")
        resources = {
            item.path: {
                "kind": item.kind,
                "content_b64": base64.b64encode(item.content).decode("ascii"),
                "digest": item.source_digest,
                "source_ref": item.source_ref.to_dict(),
            }
            for item in pack.resources
        }
        return {
            "record_type": "managed_pack.authoring",
            "pack_id": pack.pack_id,
            "pack_version": pack.version,
            "authoring_revision": revision,
            "status": status,
            "source": pack.source.to_dict(),
            "manifest": dict(pack.manifest),
            "resources": resources,
            "execution_pins": [item.to_dict() for item in pack.execution_pins],
            "compatibility": describe_compatibility(pack).to_dict(),
        }

    def adopt_pack(self, project_id: str, pack: Any, *, logical_request_key: str) -> PackRevision:
        """Create the first pack snapshot as a Runtime-owned project document."""
        document_id = self.pack_document_id(getattr(pack, "pack_id", ""))
        content = self._pack_content(pack, revision="rev-1")
        response = self.transport.create_document(
            project_id,
            document_id,
            "managed_pack.authoring",
            content,
            idempotency_key=logical_request_key,
        )
        data = _data(response)
        return PackRevision(project_id, document_id, int(data.get("version", 1)), "rev-1", "authoring", response)

    def read_pack(self, project_id: str, pack_id: str) -> Mapping[str, Any]:
        document = _data(self.transport.get_document(project_id, self.pack_document_id(pack_id)))
        content = document.get("content")
        return content if isinstance(content, Mapping) else document

    def author_pack(
        self,
        project_id: str,
        pack: Any,
        updates: Mapping[str, bytes | bytearray | str],
        *,
        expected_version: int,
        logical_request_key: str,
    ) -> PackRevision:
        """Edit only declared resources and retain all Runtime-owned siblings."""
        _require_shared()
        if not isinstance(pack, ManagedPack):
            raise SharedWorkError("pack must be the shared Herzchen ManagedPack type")
        current = self.read_pack(project_id, pack.pack_id)
        if current.get("status") == "expired":
            raise SharedWorkError("managed pack authoring copy is expired")
        declared = {item.path: item for item in pack.resources}
        unknown = sorted(set(updates).difference(declared))
        if unknown:
            raise SharedWorkError(f"authoring update is not an admitted resource: {unknown[0]}")
        resources = dict(current.get("content", {}).get("resources", current.get("resources", {})))
        for path, value in updates.items():
            if isinstance(value, str):
                value = value.encode("utf-8")
            if isinstance(value, bytearray):
                value = bytes(value)
            if not isinstance(value, bytes):
                raise SharedWorkError(f"authoring content for {path!r} must be bytes or text")
            base = dict(resources.get(path) or {})
            base["content_b64"] = base64.b64encode(value).decode("ascii")
            base["digest"] = _sha256(value)
            resources[path] = base
        content = dict(current.get("content") or current)
        content["resources"] = {path: resources[path] for path in sorted(resources)}
        content["authoring_revision"] = f"rev-{expected_version + 1}"
        content["status"] = "authoring"
        response = self.transport.update_document(
            project_id,
            self.pack_document_id(pack.pack_id),
            expected_version=expected_version,
            idempotency_key=logical_request_key,
            content=content,
        )
        data = _data(response)
        return PackRevision(project_id, self.pack_document_id(pack.pack_id), int(data.get("version", expected_version + 1)), content["authoring_revision"], "authoring", response)

    def expire_pack(self, project_id: str, pack_id: str, *, expected_version: int, logical_request_key: str) -> PackRevision:
        current = self.read_pack(project_id, pack_id)
        content = dict(current.get("content") or current)
        content["status"] = "expired"
        content["expired_revision"] = content.get("authoring_revision")
        response = self.transport.update_document(
            project_id,
            self.pack_document_id(pack_id),
            expected_version=expected_version,
            idempotency_key=logical_request_key,
            content=content,
        )
        data = _data(response)
        return PackRevision(project_id, self.pack_document_id(pack_id), int(data.get("version", expected_version + 1)), str(content.get("authoring_revision")), "expired", response)

    def pinned_pack_execution(self, project_id: str, pack_id: str) -> Mapping[str, Any]:
        """Return the immutable pack facts a Runtime task should pin."""
        content = self.read_pack(project_id, pack_id)
        resources = content.get("resources", {})
        return {
            "pack_id": content.get("pack_id", pack_id),
            "revision": content.get("authoring_revision"),
            "execution_pins": list(content.get("execution_pins", [])),
            "resource_digests": {path: value.get("digest") for path, value in resources.items() if isinstance(value, Mapping)},
        }


__all__ = [
    "PackRevision",
    "RuntimeSharedWorkAdapter",
    "SharedWorkError",
    "SharedWorkUnavailable",
    "TemplateAdmission",
]
