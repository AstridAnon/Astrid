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

from dataclasses import dataclass
from typing import Any, Mapping


class SharedWorkUnavailable(RuntimeError):
    """The installed shared Herzchen package lacks the required pack APIs."""


class SharedWorkError(ValueError):
    """A shared resource cannot be admitted to Runtime."""


try:  # The Astrid standalone distribution remains importable without FND.
    from herzchen.contracts import ResourceRef
    from herzchen.packs.authoring import ManagedPack
    from herzchen.packs.templates import (
        WorkProtocol,
        WorkTemplate,
        blank_project_template,
        render_template,
    )
except ImportError as exc:  # pragma: no cover - exercised by standalone installs
    ResourceRef = ManagedPack = WorkProtocol = WorkTemplate = blank_project_template = None  # type: ignore[assignment]
    render_template = None  # type: ignore[assignment]
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


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _record_kind(value: Any) -> str:
    kind = _field(value, "kind", "")
    return str(getattr(kind, "value", kind)).removeprefix("work.")


def _record_payload(value: Any) -> Mapping[str, Any]:
    payload = _field(value, "payload", {})
    if not isinstance(payload, Mapping):
        raise SharedWorkError("public template engine returned a record without a payload")
    return payload


def _runtime_request_key(value: str, suffix: str) -> str:
    candidate = f"{value}-{suffix}"
    if candidate and candidate[0].isalnum() and all(
        character.isalnum() or character in "._~-" for character in candidate
    ) and len(candidate) <= 256:
        return candidate
    import hashlib

    return "astrid-work-" + hashlib.sha256(candidate.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TemplateAdmission:
    """The Runtime receipts for tasks expanded from one shared template."""

    project_id: str
    template_ref: Mapping[str, Any]
    rendered: Any
    tasks: tuple[Mapping[str, Any], ...]
    template_result: Any = None


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

    def __init__(
        self,
        transport: Any,
        *,
        managed_pack_handler: Any = None,
        managed_pack_actor: Any = None,
        template_engine: Any = None,
    ) -> None:
        if transport is None:
            raise TypeError("transport is required")
        self.transport = transport
        self.managed_pack_handler = managed_pack_handler
        self.managed_pack_actor = managed_pack_actor
        self.template_engine = template_engine
        _require_shared()

    def _require_template_engine(self) -> Any:
        if self.template_engine is None:
            raise SharedWorkUnavailable(
                "Runtime must supply the public TemplateEngine owner seam"
            )
        return self.template_engine

    def _template_project_ref(self, project_id: str) -> Any:
        engine = self._require_template_engine()
        reader = getattr(engine, "reader", None)
        authority = getattr(reader, "authority", None)
        if not isinstance(authority, str) or not authority:
            raise SharedWorkUnavailable("public TemplateEngine did not expose its Runtime authority")
        return ResourceRef(authority, "work.project", project_id)

    def _require_managed_pack_handler(self) -> Any:
        handler = self.managed_pack_handler
        if handler is None:
            raise SharedWorkUnavailable(
                "Runtime must supply the public ManagedPackAuthoringHandler owner seam"
            )
        return handler

    def _managed_pack_actor(self) -> Any:
        actor = self.managed_pack_actor
        if actor is None:
            actor = getattr(self._require_managed_pack_handler(), "actor", None)
        if actor is None:
            raise SharedWorkUnavailable("public pack handler requires the Runtime-owned authenticated actor")
        return actor

    @staticmethod
    def _pack_revision(result: Any, project_id: str, pack_id: str, *, status: str = "authoring") -> PackRevision:
        revision = str(result.revision)
        if not revision.startswith("rev-"):
            raise SharedWorkError("shared pack authoring returned an unpinned revision")
        try:
            version = int(revision.removeprefix("rev-"))
        except ValueError as exc:
            raise SharedWorkError("shared pack authoring returned an invalid revision") from exc
        receipt = result.receipt
        response = receipt.to_dict() if hasattr(receipt, "to_dict") else receipt
        return PackRevision(project_id, RuntimeSharedWorkAdapter.pack_document_id(pack_id), version, revision, status, response)

    def _pack_identity(self, pack_id: str) -> Any:
        handler = self._require_managed_pack_handler()
        reader = getattr(handler, "reader", None)
        if reader is None or ResourceRef is None:
            raise SharedWorkUnavailable("public pack handler did not expose its finite Runtime reader")
        identity = reader.get_identity(ResourceRef(reader.authority, "managed_pack", pack_id))
        if identity is None:
            raise SharedWorkError(f"managed pack is not adopted: {pack_id!r}")
        return identity

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
        engine = self._require_template_engine()
        result = engine.instantiate_bundle(
            template,
            parameters,
            project=self._template_project_ref(project_id),
            logical_request_key=logical_request_key,
        )
        rendered = _field(result, "rendered")
        if rendered is None:
            raise SharedWorkError("public TemplateEngine returned no rendered template")
        records = tuple(_field(result, "records", ()) or ())
        task_records = tuple(record for record in records if _record_kind(record) == "task")
        if not task_records:
            raise SharedWorkError("shared template contains no task records")
        nodes = _task_nodes(rendered.seed)
        node_by_local = {_local_id(node, index): node for index, node in enumerate(nodes)}

        admissions: list[Mapping[str, Any]] = []
        template_ref = rendered.template.ref.to_dict()
        for index, record in enumerate(task_records):
            payload = _record_payload(record)
            fields = dict(payload.get("fields") or {})
            origin = fields.get("template_origin")
            local_id = origin.get("local_id") if isinstance(origin, Mapping) else None
            if not isinstance(local_id, str) or not local_id:
                raise SharedWorkError("public template engine returned a task without local identity")
            node = node_by_local.get(local_id)
            if node is None:
                raise SharedWorkError(f"public template engine returned an unknown task local_id: {local_id!r}")
            capability = fields.get("capability_id")
            if not isinstance(capability, str) or not capability:
                raise SharedWorkError(f"task {local_id!r} does not declare a Runtime capability_id")
            input_ids = fields.get("input_object_ids", [])
            if not isinstance(input_ids, list) or any(not isinstance(item, str) for item in input_ids):
                raise SharedWorkError(f"task {local_id!r} input_object_ids must be a list of strings")
            spec = dict(fields.get("spec") or {})
            spec["template_origin"] = template_ref
            spec["template_local_id"] = local_id
            spec["template_parameters"] = dict(rendered.parameters)
            record_ref = _field(record, "ref")
            if isinstance(record_ref, ResourceRef):
                spec["shared_work_ref"] = record_ref.to_dict()
            digest = fields.get("capability_digest") or spec.get("capability_digest")
            if not isinstance(digest, str) or not digest:
                raise SharedWorkError(f"task {local_id!r} must declare the admitted capability digest")
            effect = fields.get("settlement_effect")
            storage = fields.get("storage_estimate")
            generation_intent = fields.get("generation_intent")
            response = self.transport.admit_task(
                capability_id=capability,
                capability_digest=digest,
                input_object_ids=list(input_ids),
                idempotency_key=_runtime_request_key(logical_request_key, local_id),
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
        return TemplateAdmission(project_id, template_ref, rendered, tuple(admissions), result)

    def instantiate_blank_project(
        self,
        parameters: Mapping[str, Any] | None = None,
        *,
        project_id: str,
        logical_request_key: str,
    ) -> Any:
        """Exercise the shared built-in blank path for an existing Runtime project."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise SharedWorkError("project_id is required")
        if not isinstance(logical_request_key, str) or not logical_request_key.strip():
            raise SharedWorkError("logical_request_key is required")
        return self._require_template_engine().instantiate_bundle(
            blank_project_template(),
            parameters,
            project=self._template_project_ref(project_id),
            logical_request_key=logical_request_key,
        )

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

    def adopt_pack(self, project_id: str, pack: Any, *, logical_request_key: str) -> PackRevision:
        """Adopt through the public FND handler over Runtime's owner."""
        _require_shared()
        if not isinstance(pack, ManagedPack):
            raise SharedWorkError("pack must be the shared Herzchen ManagedPack type")
        handler = self._require_managed_pack_handler()
        result = handler.author(
            pack,
            logical_request_key=logical_request_key,
            actor=self._managed_pack_actor(),
        )
        return self._pack_revision(result, project_id, pack.pack_id)

    def read_pack(self, project_id: str, pack_id: str) -> Mapping[str, Any]:
        del project_id  # The public Runtime owner scopes the pack identity.
        handler = self._require_managed_pack_handler()
        value = handler.read(pack_id)
        if not isinstance(value, Mapping):
            raise SharedWorkError("public pack handler returned a non-object resource")
        return value

    def author_pack(
        self,
        project_id: str,
        pack: Any,
        updates: Mapping[str, bytes | bytearray | str],
        *,
        expected_version: int,
        logical_request_key: str,
    ) -> PackRevision:
        """Edit via the public FND handler and Runtime's one owner ledger."""
        _require_shared()
        if not isinstance(pack, ManagedPack):
            raise SharedWorkError("pack must be the shared Herzchen ManagedPack type")
        identity = self._pack_identity(pack.pack_id)
        if identity.version != expected_version:
            raise SharedWorkError(
                f"managed pack version mismatch: expected {expected_version}, current {identity.version}"
            )
        handler = self._require_managed_pack_handler()
        result = handler.author(
            pack,
            updates,
            logical_request_key=logical_request_key,
            actor=self._managed_pack_actor(),
        )
        return self._pack_revision(result, project_id, pack.pack_id)

    def expire_pack(self, project_id: str, pack_id: str, *, expected_version: int, logical_request_key: str) -> PackRevision:
        del project_id, pack_id, expected_version, logical_request_key
        raise SharedWorkUnavailable(
            "managed-pack expiry requires release_pack_authoring with the active EDT checkout"
        )

    def release_pack_authoring(
        self,
        project_id: str,
        pack: Any,
        *,
        authoring_lifecycle: Any,
        authoring_target: Any,
        checkout_root: Any,
        registered_files: Any,
        logical_request_key: str,
        writer_identity: str,
        mode: str = "manual",
    ) -> PackRevision:
        """Finish and retire one materialised pack checkout through EDT.

        The checkout and its authenticated writer lease belong to the host;
        Runtime still owns every durable session, finish, cleanup, and pack
        event.  This intentionally requires the host's exact checkout inputs
        instead of inventing a status-only expiry command.
        """
        _require_shared()
        if not isinstance(pack, ManagedPack):
            raise SharedWorkError("pack must be the shared Herzchen ManagedPack type")
        if authoring_lifecycle is None or authoring_target is None:
            raise SharedWorkUnavailable("the public EDT authoring lifecycle and active checkout are required")
        if not isinstance(writer_identity, str) or not writer_identity:
            raise SharedWorkError("writer_identity is required for safe checkout retirement")
        handler = self._require_managed_pack_handler()
        semantic_handler = handler.lifecycle_handler(
            pack, request_id=f"{logical_request_key}:content"
        )
        result = authoring_lifecycle.finish(
            authoring_target,
            request_id=f"{logical_request_key}:session",
            mode=mode,
            checkout_root=checkout_root,
            registered_files=registered_files,
            handler=semantic_handler,
            writer_identity=writer_identity,
        )
        finish = getattr(result, "finish", None)
        application = getattr(finish, "application", None)
        cleanup = getattr(result, "cleanup", None)
        durable_cleanup = getattr(result, "durable_cleanup", None)
        if getattr(finish, "status", None) not in {"finished", "already_finished", "replayed"}:
            raise SharedWorkError(
                f"EDT did not finish the managed-pack checkout: {getattr(finish, 'error', None)!r}"
            )
        if cleanup is None or not bool(getattr(cleanup, "complete", False)):
            raise SharedWorkError("EDT physical checkout cleanup did not complete")
        durable_cleanup_status = getattr(durable_cleanup, "cleanup", None)
        if durable_cleanup_status is None or getattr(durable_cleanup_status, "value", durable_cleanup_status) != "complete":
            raise SharedWorkError("EDT durable cleanup did not complete")
        if application is None:
            identity = self._pack_identity(pack.pack_id)
            return PackRevision(
                project_id,
                self.pack_document_id(pack.pack_id),
                identity.version,
                str(identity.ref.revision),
                "released",
                None,
            )
        return self._pack_revision(application, project_id, pack.pack_id, status="released")

    def pinned_pack_execution(self, project_id: str, pack_id: str) -> Mapping[str, Any]:
        """Return the immutable pack facts a Runtime task should pin."""
        content = self.read_pack(project_id, pack_id)
        identity = self._pack_identity(pack_id)
        resources = content.get("resources", {})
        return {
            "pack_id": content.get("pack_id", pack_id),
            # The public current read is an identity payload and therefore
            # need not duplicate its head revision.  Pin the Runtime-owned
            # identity ref, which is the authoritative current revision.
            "revision": identity.ref.revision,
            "execution_pins": list(content.get("execution_pins", [])),
            "resource_digests": {
                path: (
                    value.get("descriptor", {}).get("digest")
                    if isinstance(value.get("descriptor"), Mapping)
                    else value.get("digest")
                )
                for path, value in resources.items()
                if isinstance(value, Mapping)
            },
        }


__all__ = [
    "PackRevision",
    "RuntimeSharedWorkAdapter",
    "SharedWorkError",
    "SharedWorkUnavailable",
    "TemplateAdmission",
]
