"""
openFDA API connector for drug labeling, adverse events, and recalls.

API docs: https://open.fda.gov/apis/
Rate limits: 120 requests/minute (no key), 240 requests/minute (with OPENFDA_API_KEY)
Note: Regulatory data — NOT for comparative efficacy or individualized prescribing
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class OpenFDAConnector:
    """openFDA API connector for drug and regulatory data search."""

    BASE_URL = "https://api.fda.gov"

    # Rate limits
    RATE_LIMIT_NO_KEY = 120  # requests per minute
    RATE_LIMIT_WITH_KEY = 240  # requests per minute
    DAILY_LIMIT = 120000

    def __init__(self):
        self.api_key = getattr(settings, "openfda_api_key", None)
        self.rate_limit_per_min = self.RATE_LIMIT_WITH_KEY if self.api_key else self.RATE_LIMIT_NO_KEY
        self._request_count = 0
        self._last_reset_time = asyncio.get_event_loop().time()
        self._request_lock = asyncio.Lock()

    async def _throttle(self):
        """Enforce rate limiting."""
        async with self._request_lock:
            now = asyncio.get_event_loop().time()

            # Reset counter every minute
            if now - self._last_reset_time >= 60:
                self._request_count = 0
                self._last_reset_time = now

            # Check if we've hit the limit
            if self._request_count >= self.rate_limit_per_min:
                wait_time = 60 - (now - self._last_reset_time)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                self._request_count = 0
                self._last_reset_time = asyncio.get_event_loop().time()

            self._request_count += 1

    async def search_drug_labels(
        self,
        query: str,
        max_results: int = 10,
    ) -> Dict[str, Any]:
        """
        Search openFDA for drug labeling information.

        Args:
            query: Search query (drug name, active ingredient, etc.)
            max_results: Maximum number of results (default 10)

        Returns:
            Dict with drug labeling information

        Note:
            This is REGULATORY DATA — not for comparative efficacy or prescribing decisions.
        """
        await self._throttle()
        try:
            params = {
                "search": f'openfda.brand_name:"{query}" OR openfda.generic_name:"{query}"',
                "limit": max_results,
            }

            if self.api_key:
                params["api_key"] = self.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/drug/label.json",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            results_data = data.get("results", [])
            meta = data.get("meta", {})

            results = []
            for item in results_data:
                openfda = item.get("openfda", {})

                # Extract key information
                brand_names = openfda.get("brand_name", [])
                generic_names = openfda.get("generic_name", [])
                manufacturer = openfda.get("manufacturer_name", [])
                route = openfda.get("route", [])
                substance_name = openfda.get("substance_name", [])

                # Extract sections
                indications = item.get("indications_and_usage", [])
                warnings = item.get("warnings", [])
                dosage = item.get("dosage_and_administration", [])

                results.append({
                    "source": "openfda",
                    "record_id": item.get("id", ""),
                    "brand_name": brand_names[0] if brand_names else "",
                    "generic_name": generic_names[0] if generic_names else "",
                    "manufacturer": manufacturer[0] if manufacturer else "",
                    "route": route[0] if route else "",
                    "substance_name": substance_name[0] if substance_name else "",
                    "indications": indications[0][:500] if indications else "",  # Truncate for brevity
                    "warnings": warnings[0][:500] if warnings else "",  # Truncate for brevity
                    "dosage": dosage[0][:500] if dosage else "",  # Truncate for brevity
                    "url": f"https://api.fda.gov/drug/label.json?search=id:{item.get('id', '')}",
                    "regulatory_data": True,  # Always label as regulatory data
                })

            return {
                "source": "openfda",
                "query_used": query,
                "total_results": meta.get("results", {}).get("total", len(results)),
                "results": results,
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "note": "Regulatory data — not for comparative efficacy or individualized prescribing",
            }

        except Exception as e:
            logger.error(f"openFDA search error: {e}")
            return {
                "source": "openfda",
                "query_used": query,
                "error": str(e),
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

    async def search_drug_events(
        self,
        query: str,
        max_results: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search openFDA for adverse drug event reports.

        Args:
            query: Search query (drug name, adverse event, etc.)
            max_results: Maximum number of results (default 10)
            date_from: Start date filter (YYYYMMDD)
            date_to: End date filter (YYYYMMDD)

        Returns:
            Dict with adverse event reports

        Note:
            These are SPONTANEOUS REPORTS — not proven causation.
        """
        await self._throttle()
        try:
            search_query = f'patient.drug.openfda.brand_name:"{query}" OR patient.drug.openfda.generic_name:"{query}"'

            if date_from or date_to:
                date_filter = " AND receivedate:["
                if date_from:
                    date_filter += date_from
                else:
                    date_filter += "19000101"
                date_filter += " TO "
                if date_to:
                    date_filter += date_to
                else:
                    date_filter += "30001231"
                date_filter += "]"
                search_query += date_filter

            params = {
                "search": search_query,
                "limit": max_results,
            }

            if self.api_key:
                params["api_key"] = self.api_key

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/drug/event.json",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

            results_data = data.get("results", [])
            meta = data.get("meta", {})

            results = []
            for item in results_data:
                safety = item.get("patient", {})
                reactions = [r.get("reactionmeddrapt", "") for r in safety.get("reaction", [])]
                drugs = [d.get("openfda", {}).get("brand_name", [""])[0] for d in safety.get("drug", [])]

                results.append({
                    "source": "openfda_events",
                    "record_id": item.get("safetyreportid", ""),
                    "receivedate": item.get("receivedate", ""),
                    "reactions": reactions[:5],  # Top 5 reactions
                    "drugs": drugs[:5],  # Top 5 drugs
                    "serious": item.get("serious", ""),
                    "seriousnesscongenital": item.get("seriousnesscongenital", ""),
                    "seriousnessdeath": item.get("seriousnessdeath", ""),
                    "seriousnesshospitalization": item.get("seriousnesshospitalization", ""),
                    "url": f"https://api.fda.gov/drug/event.json?search=safetyreportid:{item.get('safetyreportid', '')}",
                    "spontaneous_report": True,  # Always label as spontaneous report
                })

            return {
                "source": "openfda_events",
                "query_used": query,
                "total_results": meta.get("results", {}).get("total", len(results)),
                "results": results,
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "note": "Spontaneous reports — not proven causation, reporting bias likely",
            }

        except Exception as e:
            logger.error(f"openFDA event search error: {e}")
            return {
                "source": "openfda_events",
                "query_used": query,
                "error": str(e),
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }


# Singleton instance
openfda_connector = OpenFDAConnector()
