"""
ClinicalTrials.gov API v2 connector for retrieving trial status and results.

API docs: https://clinicaltrials.gov/data-api/about-api
Rate limits: No documented hard limit; using conservative 5 req/s
Note: Registry data, NOT peer-reviewed evidence
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class ClinicalTrialsConnector:
    """ClinicalTrials.gov API v2 connector for clinical trial search."""

    BASE_URL = "https://clinicaltrials.gov/api/v2"
    RATE_LIMIT = 5  # requests per second (conservative)

    def __init__(self):
        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self):
        """Enforce rate limiting."""
        async with self._request_lock:
            now = asyncio.get_event_loop().time()
            wait_time = (1.0 / self.RATE_LIMIT) - (now - self._last_request_time)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request_time = asyncio.get_event_loop().time()

    async def search(
        self,
        query: str,
        max_results: int = 10,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search ClinicalTrials.gov for trials matching the query.

        Args:
            query: Search query (condition, intervention, etc.)
            max_results: Maximum number of results (default 10)
            status: Filter by trial status (e.g., "RECRUITING", "COMPLETED")
            date_from: Start date filter (YYYY-MM-DD) for last update
            date_to: End date filter (YYYY-MM-DD) for last update

        Returns:
            Dict with search results including NCT numbers, titles, status, URLs

        Note:
            Results are REGISTRY DATA, not peer-reviewed evidence.
        """
        await self._throttle()
        try:
            # Build query parameters
            params = {
                "query.term": query,
                "pageSize": max_results,
                "format": "json",
            }

            if status:
                params["query.overallStatus"] = status

            if date_from or date_to:
                if date_from and date_to:
                    params["query.lastUpdateRange"] = f"{date_from}..{date_to}"
                elif date_from:
                    params["query.lastUpdateRange"] = f"min..{date_from}"
                elif date_to:
                    params["query.lastUpdateRange"] = f"{date_to}..max"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/studies",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            studies = data.get("studies", [])
            total_results = data.get("totalCount", len(studies))

            results = []
            for study in studies:
                protocol = study.get("protocolSection", {})
                identification = protocol.get("identificationModule", {})
                status_module = protocol.get("statusModule", {})
                description = protocol.get("descriptionModule", {})

                nct_number = identification.get("nctId", "")
                if not nct_number:
                    continue

                results.append({
                    "source": "clinicaltrials",
                    "record_id": nct_number,
                    "title": identification.get("briefTitle", ""),
                    "official_title": identification.get("officialTitle", ""),
                    "status": status_module.get("overallStatus", ""),
                    "phase": protocol.get("designModule", {}).get("phases", [None])[0] if protocol.get("designModule", {}).get("phases") else "N/A",
                    "study_type": protocol.get("designModule", {}).get("studyType", ""),
                    "enrollment": protocol.get("designModule", {}).get("enrollmentInfo", {}).get("count"),
                    "start_date": status_module.get("startDateStruct", {}).get("date"),
                    "completion_date": status_module.get("completionDateStruct", {}).get("date"),
                    "last_update": status_module.get("lastUpdatePostDateStruct", {}).get("date"),
                    "condition": ", ".join(protocol.get("conditionsModule", {}).get("conditions", [])),
                    "intervention": ", ".join([i.get("name", "") for i in protocol.get("armsInterventionsModule", {}).get("interventions", [])]),
                    "url": f"https://clinicaltrials.gov/study/{nct_number}",
                    "has_results": bool(study.get("resultsFirstPostDateStruct")),
                    "registry_data": True,  # Always label as registry data
                })

            return {
                "source": "clinicaltrials",
                "query_used": query,
                "total_results": total_results,
                "results": results,
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "note": "Registry data — not peer-reviewed evidence",
            }

        except Exception as e:
            logger.error(f"ClinicalTrials.gov search error: {e}")
            return {
                "source": "clinicaltrials",
                "query_used": query,
                "error": str(e),
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }


# Singleton instance
clinicaltrials_connector = ClinicalTrialsConnector()
