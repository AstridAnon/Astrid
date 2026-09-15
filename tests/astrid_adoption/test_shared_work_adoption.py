"""AST-05 proof: shared work resources compose with one Runtime owner."""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2].parent.parent / "ast03-worktrees" / "runtime-8b"
sys.path.insert(0, str(RUNTIME_ROOT))

from herzchen.contracts import ResourceRef  # noqa: E402
from herzchen.packs.authoring import (  # noqa: E402
    ExecutionPin,
    ManagedPack,
    ManagedResource,
    ManagedSourceIdentity,
)
from herzchen.packs.templates import work_protocol, work_template  # noqa: E402
from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402

from astrid.sdk.work_adoption import RuntimeSharedWorkAdapter  # noqa: E402
from astrid.sdk.workspace_client import WorkspaceClient  # noqa: E402


CAPABILITY = "render.ast05.cpu"
CAPABILITY_DIGEST = "sha256:" + hashlib.sha256(CAPABILITY.encode()).hexdigest()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _data(value):
    return value["data"] if isinstance(value, dict) and isinstance(value.get("data"), dict) else value


def _managed_pack(tmp_path: Path) -> ManagedPack:
    root = tmp_path / "managed-pack"
    root.mkdir()
    (root / "pack.yaml").write_text("id: ast05-pack\nschema_version: 2\n", encoding="utf-8")
    old = b"old-authoring-copy"
    source_revision = "a" * 40
    source = ManagedSourceIdentity(
        pack_id="ast05-pack",
        source_kind="managed",
        source_revision=source_revision,
        source_tree_sha256="b" * 64,
        source_manifest_sha256="c" * 64,
        source_inventory_identity="d" * 64,
        pack_root=str(root),
        manifest_path=str(root / "pack.yaml"),
    )
    resource_ref = ResourceRef("astrid-managed", "managed_pack", "ast05-pack", source_revision)
    return ManagedPack(
        "ast05-pack",
        "2.0.0",
        source,
        {"id": "ast05-pack", "schema_version": 2},
        (ManagedResource("skill/SKILL.md", "skill", old, hashlib.sha256(old).hexdigest(), resource_ref),),
        (ExecutionPin("ast05-pack/skill/SKILL.md", "skill", source_revision, hashlib.sha256(old).hexdigest(), role="capability"),),
        ("normal",),
    )


def test_shared_template_pack_authoring_and_cpu_attempt_use_runtime_only(tmp_path: Path) -> None:
    realm = tmp_path / "realm"
    support = tmp_path / "support"
    RealmStore.initialize(realm, realm_id="ast05-shared-work").close()
    daemon = RuntimeDaemon(realm, support_root=support).start()
    try:
        client = WorkspaceClient(daemon.endpoint, daemon.token)
        adapter = RuntimeSharedWorkAdapter(client)
        project = _data(client.create_project("AST-05 shared work", slug="ast05-shared-work", idempotency_key="ast05-project"))
        project_id = project["project_id"]
        client.register_capability(CAPABILITY, CAPABILITY_DIGEST, idempotency_key="ast05-capability")
        client.register_executor(
            {"executor_id": "ast05-cpu-host", "capabilities": [CAPABILITY]},
            idempotency_key="ast05-executor",
        )

        protocol = work_protocol(
            "ast05-creative-protocol",
            choices={"normal": {"review_sequence": ()}},
        )
        project = adapter.adopt_protocol(project_id, protocol, logical_request_key="ast05-protocol")
        assert project["metadata"]["shared_work"]["protocol_ref"]["id"] == protocol.id

        pack = _managed_pack(tmp_path)
        adopted = adapter.adopt_pack(project_id, pack, logical_request_key="ast05-pack-adopt")
        pinned = adapter.pinned_pack_execution(project_id, pack.pack_id)
        assert adopted.revision == "rev-1"
        assert pinned["revision"] == "rev-1"

        template = work_template(
            "ast05-creative-render",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {"prompt": {"type": "string", "required": True}},
                "required": ["prompt"],
            },
            seed={
                "tasks": [
                    {
                        "local_id": "creative-render",
                        "kind": "task",
                        "title": "Creative CPU render",
                        "fields": {
                            "capability_id": CAPABILITY,
                            "capability_digest": CAPABILITY_DIGEST,
                            "spec": {"pack_pin": pinned},
                        },
                    }
                ]
            },
        )
        admitted = adapter.instantiate_tasks(
            template,
            {"prompt": "a quiet opening"},
            project_id=project_id,
            logical_request_key="ast05-template",
        )
        assert len(admitted.tasks) == 1
        task = admitted.tasks[0]
        assert task["project_id"] == project_id
        assert task["spec"]["spec"]["template_origin"]["id"] == template.id
        assert task["spec"]["spec"]["pack_pin"]["revision"] == "rev-1"

        claim = _data(client.claim_task(executor_id="ast05-cpu-host", capability_ids=[CAPABILITY], idempotency_key="ast05-claim"))
        critique = adapter.record_critique(
            project_id,
            task["task_id"],
            {"notes": "composition is coherent", "creative_acceptance": "pending"},
            logical_request_key="ast05-critique",
        )
        assert critique["kind"] == "creative.critique"
        assert _data(client.get_task(task["task_id"]))["state"] == "running"
        edited = adapter.author_pack(
            project_id,
            pack,
            {"skill/SKILL.md": b"new-authoring-copy"},
            expected_version=1,
            logical_request_key="ast05-pack-edit",
        )
        expired = adapter.expire_pack(
            project_id,
            pack.pack_id,
            expected_version=2,
            logical_request_key="ast05-pack-expire",
        )
        assert edited.revision == "rev-2"
        assert expired.status == "expired"
        assert adapter.read_pack(project_id, pack.pack_id)["status"] == "expired"

        payload = b"ast05-cpu-output"
        settled = _data(
            client.settle_attempt(
                claim["attempt_id"],
                {
                    "lease_id": claim["lease_id"],
                    "fence": claim["fence"],
                    "runtime_epoch": claim["runtime_epoch"],
                    "outputs": [
                        {
                            "name": "result",
                            "kind": "object",
                            "digest": _digest(payload),
                            "media_type": "text/plain",
                            "size": len(payload),
                            "data_base64": base64.b64encode(payload).decode("ascii"),
                        }
                    ],
                },
                idempotency_key="ast05-settle",
            )
        )
        assert settled["state"] == "succeeded"
        readback = _data(client.get_task(task["task_id"]))
        assert readback["state"] == "succeeded"
        assert readback["spec"]["spec"]["pack_pin"]["revision"] == "rev-1"
        assert readback["spec"]["spec"]["template_origin"]["revision"] == template.revision
    finally:
        daemon.stop()
