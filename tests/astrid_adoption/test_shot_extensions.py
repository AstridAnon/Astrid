"""Canonical AST-04 entry point for the Runtime-owned creative-work proof.

The assertions live in ``test_creative_work`` so the focused proof remains
easy to run while this task's catalog-facing test name stays stable.
"""

from test_creative_work import test_runtime_owned_creative_work_preserves_siblings_order_and_promotion

__all__ = ["test_runtime_owned_creative_work_preserves_siblings_order_and_promotion"]
