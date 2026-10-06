"""
Policy Engine — PHI detection, clinical guardrails, and citation enforcement.

Enforces safety boundaries at four points:
1. Before external API calls (PHI check on queries)
2. Before responding to user (citation enforcement)
3. On memory writes (prevent PHI in persistent storage)
4. On cron/scheduled output (prevent PHI in logs/briefs)
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# PHI detection patterns
# --------------------------------------------------------------------------

# Patterns that suggest patient-identifiable information
_PHI_PATTERNS = [
    # Saudi national ID (10 digits)
    (r"\b\d{10}\b", "Saudi national ID number"),
    # MRN / medical record numbers (common patterns)
    (r"\bMRN[:\s-]*\d{4,}\b", "Medical Record Number (MRN)"),
    (r"\bmedical\s+record\s+(?:number|#|no)[:\s-]*\d+", "Medical record reference"),
    # Phone numbers (various formats)
    (r"\b(?:\+966|00966|0)?5?\d{8}\b", "Saudi phone number"),
    (r"\b\+?\d{10,15}\b", "Phone number"),
    # Email addresses
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "Email address"),
    # Dates of birth (explicit mentions)
    (r"\bDOB[:\s-]+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "Date of birth"),
    (r"\bdate\s+of\s+birth[:\s-]+\d", "Date of birth"),
    (r"\bborn\s+(?:on\s+)?\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", "Date of birth"),
    # Patient name patterns
    (r"\bpatient\s+(?:named?|name)[:\s-]+[A-Z][a-z]+", "Patient name"),
    (r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", "Person name with title"),
    # Passport / ID numbers
    (r"\bpassport\s*(?:number|#|no)[:\s-]*\w+", "Passport number"),
    (r"\bID\s*(?:number|#|no)[:\s-]*\d+", "ID number"),
    # Insurance / medical ID
    (r"\binsurance\s*(?:ID|number|#)[:\s-]*\w+", "Insurance identifier"),
]

# Clinical context words that suggest a patient case (not a general question)
_PATIENT_CASE_INDICATORS = [
    "my patient",
    "the patient",
    "pt is",
    "pt has",
    "pt presents",
    "patient presents",
    "patient is a",
    "years old male",
    "years old female",
    "year old male",
    "year old female",
    "y/o male",
    "y/o female",
    "y.o. male",
    "y.o. female",
    "came to the clinic",
    "came to er",
    "came to the er",
    "admitted to",
    "was admitted",
    "his/her",
    "her/his",
]

# Clinical question starters that are OK (general evidence questions)
_GENERAL_QUESTION_INDICATORS = [
    "what is the evidence",
    "latest evidence",
    "recent studies",
    "what do studies show",
    "systematic review",
    "meta-analysis",
    "clinical trial",
    "guideline",
    "what does pubmed say",
    "search for",
    "what are the treatments for",
    "what drugs are approved for",
    "is there evidence",
    "compare the evidence",
]


class PolicyEngine:
    """Clinical evidence policy enforcement."""

    def check_phi(self, text: str) -> Dict:
        """
        Check text for potential patient-identifiable information.

        Returns:
            Dict with:
              - phi_detected: bool
              - matches: list of {pattern, description, matched_text}
              - warning: human-readable warning message
        """
        if not text:
            return {"phi_detected": False, "matches": [], "warning": ""}

        matches = []
        text_lower = text.lower()

        # Check regex patterns
        for pattern, description in _PHI_PATTERNS:
            found = re.finditer(pattern, text, re.IGNORECASE)
            for match in found:
                matched_text = match.group()
                # Avoid false positives: short digit sequences that are part of other text
                if len(matched_text) <= 3 and matched_text.isdigit():
                    continue
                matches.append({
                    "pattern": description,
                    "matched_text": matched_text[:50],  # Truncate for logging
                })

        # Check patient case indicators
        is_patient_case = any(indicator in text_lower for indicator in _PATIENT_CASE_INDICATORS)
        is_general_question = any(indicator in text_lower for indicator in _GENERAL_QUESTION_INDICATORS)

        # Rule 1: If it looks like a patient case AND has any PHI indicator → block
        if is_patient_case and (matches or is_patient_case):
            # Even without regex matches, a patient-case pattern alone is suspicious
            if not matches:
                matches.append({
                    "pattern": "patient case language",
                    "matched_text": "patient-specific phrasing detected",
                })
            warning = (
                "This appears to be a patient-specific case with potential identifiable information. "
                "Please remove patient identifiers (names, IDs, dates of birth, contact details) "
                "and rephrase as a general clinical question."
            )
            logger.warning(
                "Potential PHI detected (indicator types: %s)",
                sorted({match["pattern"] for match in matches}),
            )
            return {
                "phi_detected": True,
                "matches": matches,
                "warning": warning,
            }

        # Rule 2: If it has clear PHI patterns regardless of context
        # Even 1 strong PHI indicator (DOB, national ID, MRN, phone, email) is enough to flag
        strong_phi_patterns = {"Saudi national ID number", "Date of birth", "Medical Record Number (MRN)",
                               "Saudi phone number", "Phone number", "Email address", "Passport number"}
        strong_matches = [m for m in matches if m["pattern"] in strong_phi_patterns]

        if strong_matches:
            warning = (
                "Potential patient-identifiable information detected. "
                "Please remove identifiers before searching."
            )
            logger.warning(
                "Potential PHI detected (strong indicator types: %s)",
                sorted({match["pattern"] for match in strong_matches}),
            )
            return {
                "phi_detected": True,
                "matches": matches,
                "warning": warning,
            }

        # Rule 3: Multiple weak PHI indicators
        if len(matches) >= 2:
            warning = (
                "Potential patient-identifiable information detected. "
                "Please remove identifiers before searching."
            )
            logger.warning(
                "Potential PHI detected (multiple indicator types: %s)",
                sorted({match["pattern"] for match in matches}),
            )
            return {
                "phi_detected": True,
                "matches": matches,
                "warning": warning,
            }

        return {"phi_detected": False, "matches": [], "warning": ""}

    def check_memory_write(self, key: str, value: str) -> Dict:
        """
        Check if a memory write contains PHI.

        Returns:
            Dict with allowed: bool, warning: str
        """
        combined = f"{key} {value}"
        phi_check = self.check_phi(combined)
        if phi_check["phi_detected"]:
            return {
                "allowed": False,
                "warning": "Memory write rejected: potential patient-identifiable information detected.",
            }

        # Also check for credential storage
        credential_patterns = [
            (r"(?i)password\s*[:=]\s*\S+", "password"),
            (r"(?i)api[_-]?key\s*[:=]\s*\S+", "API key"),
            (r"(?i)secret\s*[:=]\s*\S+", "secret"),
            (r"(?i)token\s*[:=]\s*\S+", "token"),
        ]
        for pattern, desc in credential_patterns:
            if re.search(pattern, combined):
                return {
                    "allowed": False,
                    "warning": f"Memory write rejected: potential {desc} detected. Store credentials in .env only.",
                }

        return {"allowed": True, "warning": ""}

    def validate_citations(self, results: List[Dict]) -> Dict:
        """
        Validate that search results have proper citation fields.

        Returns:
            Dict with valid: bool, issues: list
        """
        issues = []
        required_fields = ["source", "record_id", "url"]

        for i, result in enumerate(results):
            for field in required_fields:
                if not result.get(field):
                    issues.append(f"Result {i}: missing required field '{field}'")

            # Check date fields
            if not result.get("published_date") and not result.get("published_or_updated"):
                issues.append(f"Result {i}: missing publication date")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
        }


# Singleton instance
policy_engine = PolicyEngine()
