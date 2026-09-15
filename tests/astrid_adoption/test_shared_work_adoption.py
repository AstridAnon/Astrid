"""AST-05 proof: shared work resources compose with one Runtime owner."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ASTRID_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ASTRID_ROOT.parent / "runtime-pack-owner"
ASTRID_SOURCE_ROOT = ASTRID_ROOT.parent.parent / "ast03-worktrees" / "astrid-cf7"
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(ASTRID_ROOT))

from herzchen.authoring import FileWriterLeaseAuthority  # noqa: E402
from herzchen.contracts import ResourceRef  # noqa: E402
from herzchen.packs.authoring import read_managed_pack  # noqa: E402
from herzchen.packs.templates import work_protocol, work_template  # noqa: E402
from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402

from astrid.core.execution.generic_host import GenericPackHost, RuntimeProtocolClient  # noqa: E402
from astrid.core.execution.guards import ExecutionGuardPolicy  # noqa: E402
from astrid.core.pack.source_setup import _tree_digest  # noqa: E402
from astrid.sdk.work_adoption import RuntimeSharedWorkAdapter  # noqa: E402
from astrid.sdk.workspace_client import WorkspaceClient  # noqa: E402


CAPABILITY = "render.ast05.cpu"
SOURCE_REVISION = "90d15804b5b76a10a14a37906f55dfa975d9aa4c"


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _data(value):
    return value["data"] if isinstance(value, dict) and isinstance(value.get("data"), dict) else value


def _canonical_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load the real accepted managed pack through canonical discovery."""
    source_root = ASTRID_SOURCE_ROOT.resolve()
    pack_root = source_root / "astrid" / "packs" / "vibecomfy"
    observed_revision = subprocess.check_output(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True
    ).strip()
    assert observed_revision == SOURCE_REVISION
    manifest = pack_root / "pack.yaml"
    state_path = tmp_path / "pack-sources.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": {
                    "vibecomfy": {
                        "pack_id": "vibecomfy",
                        "pack_root": str(pack_root),
                        "repository": "astrid-accepted-cpu-source",
                        "revision": observed_revision,
                        "pack_subpath": ".",
                        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                        "tree_sha256": _tree_digest(pack_root),
                        "source_kind": "managed",
                    }
                },
                "cached": {},
                "disabled": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASTRID_SOURCE_STATE", str(state_path))
    pack = read_managed_pack("vibecomfy", project_root=ASTRID_ROOT)
    assert pack.source.source_revision == SOURCE_REVISION
    assert pack.source.source_manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert any(item.path == "skill/SKILL.md" for item in pack.resources)
    return pack


def _cpu_render_pack(tmp_path: Path) -> Path:
    """Create a deterministic, offline CPU renderer for GenericPackHost."""
    root = tmp_path / "ast05-cpu-render-pack"
    root.mkdir()
    (root / "executor.yaml").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": CAPABILITY,
                "name": "AST05 deterministic CPU render",
                "kind": "external",
                "version": "1.0",
                "command": {
                    "argv": [
                        "{python_exec}",
                        "-c",
                        (
                            "from pathlib import Path; import hashlib, json; "
                            "source=Path('{pack_snapshot}').read_bytes(); "
                            "data=b'AST05-PINNED-SOURCE\\n'+hashlib.sha256(source).hexdigest().encode()+b'\\n'; "
                            "out=Path('{out}'); "
                            "path=out/'render.ppm'; path.write_bytes(data); "
                            "(out/'manifest.json').write_text(json.dumps({'outputs': "
                            "[{'name':'result','path':'render.ppm','content_hash':'sha256:'+hashlib.sha256(data).hexdigest(),'bytes':len(data),'ordinal':0,'is_primary':True,'role':'result'}]}))"
                        ),
                    ]
                },
                "inputs": [
                    {
                        "name": "pack_snapshot",
                        "type": "file",
                        "required": True,
                        "description": "Runtime-materialized pinned pack resource",
                    }
                ],
                "outputs": [
                    {
                        "name": "result",
                        "type": "file",
                        "path_template": "{out}/render.ppm",
                        "artifact_type": "image/x-portable-pixmap",
                    }
                ],
                "metadata": {"adapter_family": "cpu", "resource_keys": ["cpu"]},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_shared_template_pack_authoring_and_cpu_attempt_use_runtime_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    realm = tmp_path / "realm"
    support = tmp_path / "support"
    RealmStore.initialize(realm, realm_id="ast05-shared-work").close()
    daemon = RuntimeDaemon(realm, support_root=support).start()
    host: GenericPackHost | None = None
    try:
        client = WorkspaceClient(daemon.endpoint, daemon.token)
        bridge = daemon.service.herzchen
        assert bridge is not None
        assert bridge.generic_contract_error is None
        assert bridge.authoring is not None
        adapter = RuntimeSharedWorkAdapter(
            client,
            managed_pack_handler=bridge.managed_packs,
            managed_pack_actor=bridge.actor,
            template_engine=bridge.template_engine,
        )
        project = _data(
            client.create_project(
                "AST-05 shared work", slug="ast05-shared-work", idempotency_key="ast05-project"
            )
        )
        project_id = project["project_id"]

        protocol = work_protocol(
            "ast05-creative-protocol",
            choices={"normal": {"review_sequence": ()}},
        )
        project = adapter.adopt_protocol(project_id, protocol, logical_request_key="ast05-protocol")
        assert project["metadata"]["shared_work"]["protocol_ref"]["id"] == protocol.id

        cpu_pack = _cpu_render_pack(tmp_path)
        cpu_record = GenericPackHost(pack_roots=[cpu_pack]).discover()[0]
        client.register_capability(
            CAPABILITY,
            cpu_record.capability_digest,
            idempotency_key="ast05-cpu-capability",
        )

        pack = _canonical_pack(tmp_path, monkeypatch)
        adopted = adapter.adopt_pack(project_id, pack, logical_request_key="ast05-pack-adopt")
        pinned = adapter.pinned_pack_execution(project_id, pack.pack_id)
        assert adopted.revision == "rev-1"
        assert pinned["revision"] == "rev-1"
        assert pinned["resource_digests"]["skill/SKILL.md"]
        prior = bridge.managed_packs.read(pack.pack_id, revision="rev-1")
        prior_skill = base64.b64decode(prior["resources"]["skill/SKILL.md"]["content_b64"])
        source_object = _data(
            client.ingest_project_object(
                project_id,
                prior_skill,
                media_type="text/markdown",
                filename="skill-SKILL.md",
                idempotency_key="ast05-pack-source-rev1",
            )
        )
        source_digest = str(source_object.get("digest", source_object.get("object_id", "")))
        assert source_digest == _digest(prior_skill)
        assert source_digest.removeprefix("sha256:") == pinned["resource_digests"]["skill/SKILL.md"]

        blank = adapter.instantiate_blank_project(
            {"title": "AST05 blank starter"},
            project_id=project_id,
            logical_request_key="ast05-blank-starter",
        )
        assert blank.project.ref.kind == "work.project"
        assert blank.project.ref.id == project_id
        assert blank.records == ()
        assert "initial-specification" in blank.document_refs
        assert blank.association_refs

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
                            "capability_digest": cpu_record.capability_digest,
                            "input_object_ids": [source_digest],
                            "spec": {
                                "pack_pin": pinned,
                                "inputs": {
                                    "pack_snapshot": {
                                        "digest": source_digest,
                                        "filename": "skill-SKILL.md",
                                    }
                                },
                            },
                        },
                    }
                ],
                "criteria": [
                    {
                        "local_id": "creative-acceptance",
                        "kind": "criterion",
                        "title": "Creative acceptance",
                        "parent": {"$local": "creative-render"},
                        "fields": {
                            "description": "The pinned source is consumed by the CPU attempt."
                        },
                    }
                ],
                "gates": [
                    {
                        "local_id": "creative-gate",
                        "kind": "gate",
                        "title": "Creative gate",
                        "dependencies": [{"$local": "creative-acceptance"}],
                        "fields": {"level": "required"},
                    }
                ],
                "documents": [
                    {
                        "local_id": "creative-brief",
                        "title": "Creative brief",
                        "role": "creative-brief",
                        "content": {"prompt": "a quiet opening", "source": "AST05"},
                    }
                ],
                "document_links": [
                    {
                        "subject": {"$local": "creative-render"},
                        "document": {"$local": "creative-brief"},
                        "namespace": "work.tasks",
                        "key": "brief",
                        "binding": "current",
                        "access_mode": "read",
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
        template_result = admitted.template_result
        assert template_result is not None
        assert {record.kind.value for record in template_result.records} == {"task", "criterion", "gate"}
        assert "creative-brief" in template_result.document_refs
        assert "creative-render:work.tasks:brief" in template_result.association_refs

        critique = adapter.record_critique(
            project_id,
            task["task_id"],
            {"notes": "composition is coherent", "creative_acceptance": "pending"},
            logical_request_key="ast05-critique",
        )
        assert critique["kind"] == "creative.critique"

        # The pack edit is a real EDT checkout. Its semantic handler is the
        # public PKG handler, and the Runtime owner records both event streams
        # in the same realm database before the exact registered file is
        # physically removed.
        checkout = tmp_path / "ast05-pack-checkout"
        checkout.mkdir()
        updated = b"# AST05 authored CPU-safe work\n"
        checkout_payload = json.dumps(
            {"updates": {"skill/SKILL.md": base64.b64encode(updated).decode("ascii")}}
        ).encode("utf-8")
        checkout_file = checkout / "pack-content.json"
        checkout_file.write_bytes(checkout_payload)
        leases = FileWriterLeaseAuthority(
            support / "writer-locks",
            authority="ast05-writer-lease",
            secret=b"ast05-writer-lease-secret-0123456789",
            writer_identities=("ast05-runtime-host",),
        )
        lifecycle = bridge.make_authoring_lifecycle(
            writer_leases=leases, writer_identity="ast05-runtime-host"
        )
        pack_identity = bridge.managed_packs.reader.get_identity(
            ResourceRef(bridge.authority, "managed_pack", pack.pack_id)
        )
        opened = lifecycle.open(
            ResourceRef(bridge.authority, "managed_pack", pack.pack_id, pack_identity.ref.revision),
            bridge.actor,
            request_id="ast05-pack-open",
            base_revision="rev-1",
            initial_content=checkout_payload,
            materialize=lambda _checkout, _initial_bytes, **_: {
                "path": str(checkout),
                "registered_files": ("pack-content.json",),
            },
        )
        released = adapter.release_pack_authoring(
            project_id,
            pack,
            authoring_lifecycle=lifecycle,
            authoring_target=opened,
            checkout_root=checkout,
            registered_files=("pack-content.json",),
            logical_request_key="ast05-pack",
            writer_identity="ast05-runtime-host",
        )
        assert released.status == "released"
        assert released.revision == "rev-2"
        assert not checkout_file.exists()

        current = bridge.managed_packs.read(pack.pack_id, revision="rev-2")
        assert prior_skill != updated
        assert base64.b64decode(current["resources"]["skill/SKILL.md"]["content_b64"]) == updated
        authoring_events = bridge.authoring.reader.list_events(
            stream=f"authoring:{pack.pack_id}"
        )
        assert [event.event_type for event in authoring_events] == [
            "authoring.open",
            "authoring.metadata",
            "authoring.finish.claim",
            "authoring.finish",
            "authoring.cleanup",
        ]
        transaction_rows = bridge._generic_writer.store.conn.execute(
            "SELECT transaction_id FROM herzchen_events WHERE stream=? ORDER BY sequence",
            (f"authoring:{pack.pack_id}",),
        ).fetchall()
        assert all(row["transaction_id"] for row in transaction_rows)
        assert (bridge._generic_writer.store.root / "realm.sqlite3").is_file()

        host = GenericPackHost(
            pack_roots=[cpu_pack],
            client=RuntimeProtocolClient(daemon.endpoint, daemon.token),
            executor_id="ast05-cpu-host",
            attempt_root=tmp_path / "ast05-attempt",
            execution_policy=ExecutionGuardPolicy(
                scratch_floor_bytes=1,
                evidence_cap_bytes=1024 * 1024,
                deadline_seconds=30.0,
            ),
        )
        registration = host.register()
        assert registration["registration"].executor_id == "ast05-cpu-host"
        outcomes = host.run(once=True)
        assert len(outcomes) == 1
        assert outcomes[0].state == "succeeded"
        cpu_task = _data(client.get_task(task["task_id"]))
        assert cpu_task["state"] == "succeeded"
        output = cpu_task["result"]["outputs"][0]
        expected_output = (
            b"AST05-PINNED-SOURCE\n"
            + hashlib.sha256(prior_skill).hexdigest().encode("ascii")
            + b"\n"
        )
        assert output["digest"] == _digest(expected_output)
        assert output["size"] == len(expected_output)
        assert _data(client.get_object(output["digest"]))["data"] == expected_output
        # The accepted task still points at the immutable pre-edit pack pin.
        assert cpu_task["spec"]["spec"]["pack_pin"]["revision"] == "rev-1"
        assert cpu_task["spec"]["spec"]["inputs"]["pack_snapshot"]["digest"] == source_digest
    finally:
        if host is not None:
            host.shutdown()
        daemon.stop()
