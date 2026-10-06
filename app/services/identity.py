"""Load the operator-maintained identity and skill documents for the agent prompt."""

from pathlib import Path
from typing import Tuple


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_FILE_CHARS = 20_000

_WORKSPACE_DOCUMENTS: Tuple[Tuple[str, str], ...] = (
    ("Agent identity and safety rules", "workspace/SOUL.md"),
    ("User-approved profile", "workspace/USER.md"),
    ("Durable workspace memory", "workspace/MEMORY.md"),
    ("Personal assistant skill", "skills/personal-assistant/SKILL.md"),
    ("Clinical evidence skill", "skills/clinical-evidence/SKILL.md"),
    ("Clinical evidence policy", "clinical/evidence-policy.md"),
)


def _read_document(relative_path: str) -> str:
    path = _PROJECT_ROOT / relative_path
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(content) > _MAX_FILE_CHARS:
        content = content[:_MAX_FILE_CHARS].rstrip() + "\n[Document truncated.]"
    return content


def load_workspace_context() -> str:
    """Return the current workspace identity/skills, so edits take effect without a deploy."""
    sections = []
    for title, relative_path in _WORKSPACE_DOCUMENTS:
        content = _read_document(relative_path)
        if content:
            sections.append(f"## {title} ({relative_path})\n{content}")
    return "\n\n".join(sections)
