from __future__ import annotations

AI_AUDIT_VERSION = "2026-06-30-clean-ai-placeholder-v1"

def placeholder_summary(client_name: str, metrics: dict, actions_count: int) -> str:
    return (
        f"{client_name} currently shows TACOS of {metrics.get('tacos',0):.1%}, "
        f"ACOS of {metrics.get('acos',0):.1%}, and CVR of {metrics.get('cvr',0):.1%}. "
        f"The rules engine found {actions_count} optimization opportunities. "
        "Prioritize high-click/no-order waste first, then scale efficient targets that are comfortably below goal."
    )
