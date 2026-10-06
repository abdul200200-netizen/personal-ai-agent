"""Load operator-maintained identity and only the skill documents needed per request."""

import re
from pathlib import Path
from typing import Iterable, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_FILE_CHARS = 20_000

_CORE_DOCUMENTS: Tuple[Tuple[str, str], ...] = (
    ("Agent identity and safety rules", "workspace/SOUL.md"),
    ("User-approved profile", "workspace/USER.md"),
    ("Durable workspace memory", "workspace/MEMORY.md"),
)
_SKILL_DOCUMENTS = {
    "personal-assistant": ("Personal assistant skill", "skills/personal-assistant/SKILL.md"),
    "clinical-evidence": ("Clinical evidence skill", "skills/clinical-evidence/SKILL.md"),
}

_CLINICAL_TERMS = re.compile(
    r"\b(clinical|medical|medicine|patient|diagnos\w*|treat\w*|drug|medication|"
    r"adverse event|pubmed|clinical trial|evidence|guideline|openfda|health|"
    r"heart failure|hfref|hfpef|diabetes|hypertension|cancer|oncolog\w*|dose|"
    r"side effect|drug safety)\b",
    re.IGNORECASE,
)
_PRODUCTIVITY_TERMS = re.compile(
    r"\b(task|todo|to-do|calendar|schedule|meeting|spreadsheet|memory|remember|"
    r"forget|brief|plan|priority|reminder)\b",
    re.IGNORECASE,
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


def select_active_skills(user_message: str) -> Tuple[str, ...]:
    """Select a small workflow context based on the current request."""
    clinical = bool(_CLINICAL_TERMS.search(user_message or ""))
    productivity = bool(_PRODUCTIVITY_TERMS.search(user_message or ""))
    if clinical and productivity:
        return ("personal-assistant", "clinical-evidence")
    if clinical:
        return ("clinical-evidence",)
    return ("personal-assistant",)


def load_workspace_context(active_skills: Iterable[str] = ("personal-assistant",)) -> str:
    """Read core profile files and only the requested skills, on every request."""
    documents = list(_CORE_DOCUMENTS)
    selected = set(active_skills)
    for skill_name in _SKILL_DOCUMENTS:
        if skill_name in selected:
            documents.append(_SKILL_DOCUMENTS[skill_name])
    if "clinical-evidence" in selected:
        documents.append(("Clinical evidence policy", "clinical/evidence-policy.md"))

    sections = []
    for title, relative_path in documents:
        content = _read_document(relative_path)
        if content:
            sections.append(f"## {title} ({relative_path})\n{content}")
    return "\n\n".join(sections)
