"""One-call entrypoint: register the kb_native types + start the HealthProbe.

Called from server.py and worker.py before any adapter/engine is built.
Phase 1: probe is a no-op stub (Phase 3 fills it in).
"""

from __future__ import annotations

from kb_platform.llm.registry import register_native

_bootstrapped = False


def bootstrap() -> None:
    """Register kb_native factory entries; idempotent.

    Phase 3 will add HealthProbe startup here. For now the probe is a no-op.
    """
    global _bootstrapped
    if _bootstrapped:
        return
    register_native()
    # HealthProbe start lands in Phase 3 (no-op until then).
    _bootstrapped = True


__all__ = ["bootstrap"]
