"""CPU proof that Astrid's task consumer uses shared identity contracts."""

from __future__ import annotations

from pathlib import Path

from astrid.sdk.herzchen_adapter import preserve_runtime_project_id, runtime_project_ref
from astrid.sdk.remote import RemoteTasks


def test_remote_task_consumer_preserves_canonical_runtime_project_id() -> None:
    project_id = "runtime-project-123"
    ref = runtime_project_ref(project_id)
    assert ref.id == project_id
    assert preserve_runtime_project_id(project_id) == project_id

    class FakeRuntimeClient:
        def __init__(self) -> None:
            self.admitted = None

        def list_capabilities(self, *, cursor=None, limit=50):
            return ([{"capability_id": "cpu.test", "definition_digest": "sha256:cap"}], None)

        def admit_task(self, **kwargs):
            self.admitted = kwargs
            return {"task": {"id": "task-1"}}

    client = FakeRuntimeClient()
    result = RemoteTasks(client).create(
        project_id=project_id,
        capability="cpu.test",
        spec={"inputs": {}},
        idempotency_key="consumer-1",
    )
    assert result.ok
    assert client.admitted["project_id"] == project_id


def test_astrid_consumer_has_no_local_runtime_store_import() -> None:
    source = Path(__file__).parents[2] / "astrid" / "sdk" / "herzchen_adapter.py"
    text = source.read_text(encoding="utf-8")
    assert "from herzchen.contracts import ResourceRef" in text
    assert "runtime_protocol.store" not in text
    assert "sqlite3" not in text
