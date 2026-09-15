"""AST-03 adoption proof for the existing Runtime-owned Astrid boundary.

These tests intentionally exercise the already-shipped composition rather
than introducing a second local store, project writer, or transport seam.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from astrid.sdk.remote import RemoteTasks
from astrid.sdk.workspace_client import WorkspaceClient
from banodoco_workspace_client import WorkspaceClient as GeneratedWorkspaceClient


class _GeneratedDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def admit_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("admit_task", (), dict(kwargs)))
        return {"task_id": "task-1", "project_id": kwargs["project_id"]}

    def current_project(self) -> dict[str, Any]:
        self.calls.append(("current_project", (), {}))
        return {"project_id": "project-1"}

    def list_capabilities(self, *, cursor=None, limit=50):
        self.calls.append(("list_capabilities", (), {"cursor": cursor, "limit": limit}))
        return [[{"capability_id": "render.basic", "definition_digest": "digest-1"}], None]


def test_runtime_host_bridge_uses_generated_client_without_local_storage_authority() -> None:
    source_path = Path(__file__).resolve().parents[2] / "astrid" / "core" / "execution" / "generic_host.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "sqlite3" not in imported
    assert "runtime_protocol.store" not in imported
    assert "runtime_protocol.service" not in imported
    bridge_source = source_path.read_text(encoding="utf-8")
    assert "banodoco_workspace_client" in bridge_source
    assert "self.generated" in bridge_source


def test_workspace_client_and_remote_tasks_preserve_canonical_project_identity() -> None:
    generated = _GeneratedDouble()
    client = WorkspaceClient.__new__(WorkspaceClient)
    client._generated = generated

    direct = client.admit_task(
        capability_id="render.basic",
        capability_digest="digest-1",
        input_object_ids=["sha256:input"],
        idempotency_key="task-1",
        project_id="project-1",
        spec={"message": "cpu adoption proof"},
    )
    assert direct["project_id"] == "project-1"

    result = RemoteTasks(generated).create(
        project_id="project-1",
        capability="render.basic",
        spec={"message": "cpu adoption proof"},
        input_manifest=["sha256:input"],
        settlement_effect={"effect_type": "generation.create_with_variant"},
        idempotency_key="task-2",
    )
    assert result.ok
    admit_calls = [call for call in generated.calls if call[0] == "admit_task"]
    assert len(admit_calls) == 2
    assert admit_calls[-1][2]["project_id"] == "project-1"
    assert admit_calls[-1][2]["input_object_ids"] == ["sha256:input"]
    assert admit_calls[-1][2]["settlement_effect"]["effect_type"] == "generation.create_with_variant"


def test_vendored_generated_client_has_runtime_lifecycle_and_receipt_operations() -> None:
    required = {
        "admit_task",
        "claim_task",
        "get_task",
        "get_object",
        "list_events",
        "settle_attempt",
        "fail_attempt",
        "heartbeat_attempt",
        "current_project",
        "create_generation",
        "create_variant",
    }
    missing = sorted(name for name in required if not callable(getattr(GeneratedWorkspaceClient, name, None)))
    assert missing == []


def test_astrid_sdk_contains_no_parallel_storage_or_raw_request_writer() -> None:
    sdk_root = Path(__file__).resolve().parents[2] / "astrid" / "sdk"
    forbidden = {"sqlite3", "runtime_protocol.store", "runtime_protocol.service"}
    findings: list[str] = []
    for path in sorted(sdk_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name in forbidden for alias in node.names):
                    findings.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module in forbidden:
                findings.append(f"{path.name}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "request"
            ):
                findings.append(f"{path.name}:{node.lineno}:raw-request")
    assert findings == []
