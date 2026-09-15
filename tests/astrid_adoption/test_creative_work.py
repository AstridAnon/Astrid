"""AST-04 CPU proof for Runtime-owned shot metadata and documents.

The journey uses Astrid's thin remote adapters against a real Runtime daemon.
It deliberately reads and mutates only through generated-client operations so
Runtime remains the sole owner of receipts, versions, media order, and
primary-candidate promotion.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


RUNTIME_ROOT = Path(
    os.environ.get(
        "ASTRID_AST04_RUNTIME_ROOT",
        "/Users/hannahomalley/Documents/Codex/2026-09-09/proceed-with-the-astrid-unified-execution/outputs/ast03-worktrees/runtime-8b",
    )
).resolve()
sys.path.insert(0, str(RUNTIME_ROOT))

from astrid.sdk.remote import RemoteAstridClient  # noqa: E402
from astrid.sdk.workspace_client import WorkspaceClient  # noqa: E402
from runtime_protocol.daemon import RuntimeDaemon  # noqa: E402
from runtime_protocol.store import RealmStore  # noqa: E402


def test_runtime_owned_creative_work_preserves_siblings_order_and_promotion(tmp_path: Path) -> None:
    realm = tmp_path / "realm"
    support = tmp_path / "support"
    RealmStore.initialize(realm, realm_id="ast04-creative-work").close()
    daemon = RuntimeDaemon(realm, support_root=support).start()
    try:
        transport = WorkspaceClient(daemon.endpoint, daemon.token)
        product = RemoteAstridClient(transport)
        project_response = transport.create_project(
            "AST-04 Creative Work",
            slug="ast04-creative-work",
            metadata={"portfolio": {"keep": True}},
            idempotency_key="ast04-project",
        )
        project = project_response["data"]
        project_id = project["project_id"]
        product_project = product.projects.show(project_id)
        assert product_project.ok and product_project.data["project_id"] == project_id
        project_metadata = product.projects.update_metadata_namespace(
            project_id,
            "creative",
            {"editor": "Astrid"},
            expected_version=1,
            idempotency_key="ast04-project-creative-editor",
        )
        assert project_metadata.ok
        assert project_metadata.data["metadata"] == {
            "portfolio": {"keep": True},
            "creative": {"editor": "Astrid"},
        }

        shot = product.shots.create(
            project=project_id,
            shot={"shot_id": "opening"},
            name="Opening",
            metadata={"creative": {"director": "Astrid", "keep": "sibling"}, "legacy": {"keep": True}},
            idempotency_key="ast04-shot",
        )
        assert shot.ok and shot.receipt is not None
        shot_id = shot.data["shot_id"]

        metadata = product.shots.update_metadata_namespace(
            project_id,
            shot_id,
            "creative",
            {"review": "pending"},
            expected_version=1,
            idempotency_key="ast04-shot-creative-review",
        )
        assert metadata.ok and metadata.receipt is not None
        assert metadata.data["metadata"] == {
            "creative": {"director": "Astrid", "keep": "sibling", "review": "pending"},
            "legacy": {"keep": True},
        }
        metadata_replay = product.shots.update_metadata_namespace(
            project_id,
            shot_id,
            "creative",
            {"review": "pending"},
            expected_version=1,
            idempotency_key="ast04-shot-creative-review",
        )
        assert metadata_replay.ok and metadata_replay.data == metadata.data
        assert metadata_replay.receipt is not None
        assert metadata_replay.receipt.receipt_id == metadata.receipt.receipt_id

        old_media = transport.ingest_project_object(
            project_id, b"old-primary", media_type="image/png", idempotency_key="ast04-old-media"
        )
        candidate_media = transport.ingest_project_object(
            project_id, b"candidate", media_type="image/png", idempotency_key="ast04-candidate-media"
        )
        old_item = product.shots.add_item(
            project_id,
            shot_id,
            media_id=old_media["data"]["digest"],
            metadata={"role": "primary_visual", "status": "primary"},
            idempotency_key="ast04-old-item",
        )
        candidate_item = product.shots.add_item(
            project_id,
            shot_id,
            media_id=candidate_media["data"]["digest"],
            metadata={
                "role": "primary_visual",
                "status": "candidate",
                "recipe": {"project_id": project_id, "shot_id": shot_id, "target_role": "primary_visual"},
            },
            idempotency_key="ast04-candidate-item",
        )
        assert old_item.ok and candidate_item.ok
        item_order = [item["item_id"] for item in candidate_item.data["items"]]
        candidate_item_id = candidate_item.data["items"][-1]["item_id"]
        promoted = product.shots.promote_candidate(
            project_id,
            shot_id,
            candidate_item_id,
            expected_head_seq=int(candidate_item.data["version"]),
            idempotency_key="ast04-promote-candidate",
        )
        assert promoted.ok and promoted.receipt is not None
        shown = product.shots.show(project_id, shot_id)
        assert shown.ok
        assert [item["item_id"] for item in shown.data["items"]] == item_order
        statuses = [item["metadata"].get("status") for item in shown.data["items"]]
        assert statuses.count("primary") == 1 and "superseded" in statuses

        forged = product.shots.update_metadata_namespace(
            project_id,
            shot_id,
            "creative",
            {"primary_item_id": "forged"},
            idempotency_key="ast04-forged-primary",
        )
        assert not forged.ok and forged.error.code == "validation_error"
        unchanged = product.shots.show(project_id, shot_id)
        assert unchanged.data["items"] == shown.data["items"]

        document = product.documents.create(
            project=project_id,
            document_id="opening-brief",
            kind="shot.brief",
            content={"creative": {"tone": "quiet"}, "legacy": {"keep": "document-sibling"}},
            idempotency_key="ast04-document",
        )
        assert document.ok and document.receipt is not None
        document_update = product.documents.update_namespace(
            project_id,
            "opening-brief",
            "creative",
            {"beat": "hold"},
            expected_version=1,
            idempotency_key="ast04-document-creative-beat",
        )
        assert document_update.ok and document_update.receipt is not None
        assert document_update.data["content"] == {
            "creative": {"tone": "quiet", "beat": "hold"},
            "legacy": {"keep": "document-sibling"},
        }
        document_replay = product.documents.update_namespace(
            project_id,
            "opening-brief",
            "creative",
            {"beat": "hold"},
            expected_version=1,
            idempotency_key="ast04-document-creative-beat",
        )
        assert document_replay.ok and document_replay.data == document_update.data
        assert document_replay.receipt is not None
        assert document_replay.receipt.receipt_id == document_update.receipt.receipt_id
        stale_document_update = product.documents.update_namespace(
            project_id,
            "opening-brief",
            "creative",
            {"beat": "changed"},
            expected_version=1,
            idempotency_key="ast04-document-stale-version",
        )
        assert not stale_document_update.ok
        assert product.documents.show(project_id, "opening-brief").data == document_update.data

        binding = product.shots.set_text_binding(
            project_id,
            shot_id=shot_id,
            kind="voiceover_script",
            text="Opening narration.",
            expected_head=0,
            idempotency_key="ast04-text-binding",
        )
        assert binding.ok and binding.receipt is not None
        readable = product.shots.show_text_binding(project_id, binding.data["binding_id"], include_text=True)
        assert readable.ok and readable.data["text"] == "Opening narration."

        shots, shot_cursor = product.shots.list(project_id).data
        documents, document_cursor = product.documents.list(project_id).data
        assert shot_cursor is None and any(row["shot_id"] == shot_id for row in shots)
        assert document_cursor is None and any(row["document_id"] == "opening-brief" for row in documents)
    finally:
        daemon.stop()
