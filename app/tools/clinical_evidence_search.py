"""
Clinical Evidence Search — orchestration layer.

Combines PubMed, Europe PMC, ClinicalTrials.gov, and openFDA into a single
evidence search tool with proper citation, PHI detection, and policy enforcement.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.connectors.clinical.pubmed import pubmed_connector
from app.connectors.clinical.europepmc import europepmc_connector
from app.connectors.clinical.clinicaltrials import clinicaltrials_connector
from app.connectors.clinical.openfda import openfda_connector
from app.core.policy import policy_engine

logger = logging.getLogger(__name__)

# Default lookback window (years)
DEFAULT_LOOKBACK_YEARS = 5


def _default_date_from(years: int = DEFAULT_LOOKBACK_YEARS) -> str:
    """Return a date string N years ago in YYYY-MM-DD format."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    target = now.replace(year=now.year - years)
    return target.strftime("%Y-%m-%d")


def _default_date_to() -> str:
    """Return today's date in YYYY-MM-DD format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_pubmed_date(pub_date: str) -> str:
    """Normalize PubMed date formats (e.g., '2025 Mar', '2025') to YYYY-MM-DD or YYYY-MM."""
    if not pub_date:
        return ""
    parts = pub_date.strip().split()
    if len(parts) == 1 and parts[0].isdigit():
        return f"{parts[0]}-01-01"
    if len(parts) >= 2:
        months = {
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "may": "05", "jun": "06", "jul": "07", "aug": "08",
            "sep": "09", "oct": "10", "nov": "11", "dec": "12",
        }
        year = parts[0] if parts[0].isdigit() else parts[1] if len(parts) > 1 and parts[1].isdigit() else ""
        month = months.get(parts[1][:3].lower(), "") if len(parts) > 1 else ""
        if year and month:
            return f"{year}-{month}-01"
        if year:
            return f"{year}-01-01"
    return pub_date


async def search_clinical_evidence(
    question: str,
    population: Optional[str] = None,
    intervention: Optional[str] = None,
    comparator: Optional[str] = None,
    outcomes: Optional[List[str]] = None,
    jurisdiction: Optional[str] = None,
    sources: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    study_types: Optional[List[str]] = None,
    max_results_per_source: int = 5,
) -> Dict[str, Any]:
    """
    Search multiple clinical evidence sources and return consolidated, cited results.

    Args:
        question: The clinical question in natural language
        population: PICO-P (patient population)
        intervention: PICO-I (intervention/treatment)
        comparator: PICO-C (comparator)
        outcomes: PICO-O (outcomes of interest)
        jurisdiction: Geographic or regulatory jurisdiction
        sources: List of sources to search (default: all)
        date_from: Start date filter (YYYY-MM-DD)
        date_to: End date filter (YYYY-MM-DD)
        study_types: Filter by study type
        max_results_per_source: Max results from each source (default 5)

    Returns:
        Consolidated evidence response with citations and metadata
    """
    # --- PHI check ---
    phi_check = policy_engine.check_phi(question)
    if phi_check.get("phi_detected"):
        return {
            "error": "Potential patient-identifiable information detected. "
                     "Please de-identify the query before searching clinical evidence.",
            "phi_warning": phi_check.get("warning", ""),
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "results": [],
            "warnings": ["Query rejected: potential PHI detected"],
        }

    # --- Default parameters ---
    if sources is None:
        sources = ["pubmed", "europe_pmc", "clinicaltrials"]

    if not date_from:
        date_from = _default_date_from()
    if not date_to:
        date_to = _default_date_to()

    # --- Build search query ---
    # Combine PICO elements with the question
    query_parts = [question]
    if intervention and intervention.lower() not in question.lower():
        query_parts.append(intervention)
    if population and population.lower() not in question.lower():
        query_parts.append(population)
    search_query = " ".join(query_parts)

    # --- Execute searches in parallel ---
    tasks = []
    warnings = []

    if "pubmed" in sources:
        pubmed_date_from = date_from.replace("-", "/")
        pubmed_date_to = date_to.replace("-", "/")
        tasks.append(("pubmed", pubmed_connector.search(
            query=search_query,
            max_results=max_results_per_source,
            date_from=pubmed_date_from,
            date_to=pubmed_date_to,
            study_types=study_types,
        )))

    if "europe_pmc" in sources:
        tasks.append(("europe_pmc", europepmc_connector.search(
            query=search_query,
            max_results=max_results_per_source,
            date_from=date_from,
            date_to=date_to,
        )))

    if "clinicaltrials" in sources:
        tasks.append(("clinicaltrials", clinicaltrials_connector.search(
            query=search_query,
            max_results=max_results_per_source,
            date_from=date_from,
            date_to=date_to,
        )))

    if "openfda" in sources:
        # Use intervention or question for drug searches
        drug_query = intervention if intervention else question
        tasks.append(("openfda", openfda_connector.search_drug_labels(
            query=drug_query,
            max_results=max_results_per_source,
        )))

    if not tasks:
        return {
            "error": "No valid sources specified. Valid sources: pubmed, europe_pmc, clinicaltrials, openfda",
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "results": [],
            "warnings": ["No valid sources"],
        }

    # Execute all searches concurrently
    results_by_source = {}
    search_results = await asyncio.gather(
        *[task for _, task in tasks],
        return_exceptions=True,
    )

    for (source_name, _), result in zip(tasks, search_results):
        if isinstance(result, Exception):
            logger.error(f"Source {source_name} failed: {result}")
            warnings.append(f"{source_name}: search failed ({str(result)[:100]})")
            results_by_source[source_name] = {"error": str(result)}
        else:
            results_by_source[source_name] = result
            if result.get("error"):
                warnings.append(f"{source_name}: {result['error'][:100]}")

    # --- Consolidate results ---
    all_results = []
    sources_searched = []
    total_results = 0

    for source_name, result in results_by_source.items():
        if "error" in result and "results" not in result:
            continue
        sources_searched.append(source_name)
        source_results = result.get("results", [])
        total_results += result.get("total_results", len(source_results))

        for item in source_results:
            # Normalize dates
            if "published_date" in item:
                item["published_date"] = _normalize_pubmed_date(item.get("published_date", ""))

            # Ensure evidence labels
            if source_name == "clinicaltrials":
                item["evidence_type"] = "registry_data"
            elif source_name == "openfda":
                item["evidence_type"] = "regulatory_data"
            elif source_name == "pubmed":
                item["evidence_type"] = "peer_reviewed"
            elif source_name == "europe_pmc":
                item["evidence_type"] = "peer_reviewed"

            all_results.append(item)

    return {
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "query_used": search_query,
        "sources_searched": sources_searched,
        "date_range": {"from": date_from, "to": date_to},
        "total_results": total_results,
        "results": all_results,
        "warnings": warnings,
        "jurisdiction": jurisdiction,
        "disclaimer": (
            "This is research assistance, not clinical advice. "
            "Consult a qualified clinician for patient-specific decisions."
        ),
    }


async def search_drugs(
    drug_name: str,
    include_events: bool = False,
    max_results: int = 5,
) -> Dict[str, Any]:
    """
    Search for drug information via openFDA.

    Args:
        drug_name: Drug name (brand or generic)
        include_events: Also search adverse event reports
        max_results: Max results per query

    Returns:
        Drug labeling information (and optionally adverse events)
    """
    # PHI check
    phi_check = policy_engine.check_phi(drug_name)
    if phi_check.get("phi_detected"):
        return {
            "error": "Potential patient-identifiable information detected.",
            "phi_warning": phi_check.get("warning", ""),
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "results": [],
            "warnings": ["Query rejected: potential PHI detected"],
        }

    labels_result = await openfda_connector.search_drug_labels(drug_name, max_results)

    results = {
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "query_used": drug_name,
        "labels": labels_result,
        "disclaimer": (
            "Regulatory data — not for comparative efficacy or individualized prescribing. "
            "Consult a qualified clinician for treatment decisions."
        ),
    }

    if include_events:
        events_result = await openfda_connector.search_drug_events(drug_name, max_results)
        results["adverse_events"] = events_result

    return results
