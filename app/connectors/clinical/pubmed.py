"""
PubMed E-utilities connector for retrieving peer-reviewed literature.

API docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/
Rate limits: 3 requests/second (no API key), 10 requests/second (with NCBI_API_KEY)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class PubMedConnector:
    """PubMed E-utilities connector for clinical literature search."""

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self):
        self.api_key = getattr(settings, "ncbi_api_key", None)
        self.rate_limit = 10 if self.api_key else 3  # requests per second
        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self):
        """Enforce rate limiting."""
        async with self._request_lock:
            now = asyncio.get_event_loop().time()
            wait_time = (1.0 / self.rate_limit) - (now - self._last_request_time)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request_time = asyncio.get_event_loop().time()

    def _build_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add API key to params if configured."""
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    async def search(
        self,
        query: str,
        max_results: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        study_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Search PubMed for articles matching the query.

        Args:
            query: Search query (can use PubMed syntax)
            max_results: Maximum number of results (default 10)
            date_from: Start date filter (YYYY/MM/DD)
            date_to: End date filter (YYYY/MM/DD)
            study_types: Filter by study type (e.g., "Clinical Trial", "Meta-Analysis")

        Returns:
            Dict with search results including PMIDs, titles, dates, URLs
        """
        # Build search query with filters
        search_query = query

        # Add date range filter
        if date_from or date_to:
            date_filter = "("
            if date_from:
                date_filter += f'"{date_from}"[Date - Publication] : '
            else:
                date_filter += '"1900/01/01"[Date - Publication] : '
            if date_to:
                date_filter += f'"{date_to}"[Date - Publication]'
            else:
                date_filter += '"3000/12/31"[Date - Publication]'
            date_filter += ")"
            search_query = f"{search_query} AND {date_filter}"

        # Add study type filters
        if study_types:
            for study_type in study_types:
                search_query += f' AND ("{study_type}"[Publication Type])'

        # Step 1: ESearch to get PMIDs
        esearch_params = {
            "db": "pubmed",
            "term": search_query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
        }

        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                esearch_response = await client.get(
                    f"{self.BASE_URL}/esearch.fcgi",
                    params=self._build_params(esearch_params),
                )
                esearch_response.raise_for_status()
                esearch_data = esearch_response.json()

            pmids = esearch_data.get("esearchresult", {}).get("idlist", [])
            total_results = int(esearch_data.get("esearchresult", {}).get("count", 0))

            if not pmids:
                return {
                    "source": "pubmed",
                    "query_used": search_query,
                    "total_results": total_results,
                    "results": [],
                    "searched_at": datetime.now(timezone.utc).isoformat(),
                }

            # Step 2: ESummary to get article details
            await self._throttle()
            esummary_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "json",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                esummary_response = await client.get(
                    f"{self.BASE_URL}/esummary.fcgi",
                    params=self._build_params(esummary_params),
                )
                esummary_response.raise_for_status()
                esummary_data = esummary_response.json()

            # Parse results
            results = []
            doc_summaries = esummary_data.get("result", {})

            for pmid in pmids:
                doc = doc_summaries.get(pmid, {})
                if not doc or pmid == "uids":
                    continue

                # Extract publication date
                pub_date = doc.get("pubdate", "")
                if " " in pub_date:  # e.g., "2025 Mar"
                    pub_date = pub_date.split()[0]

                results.append({
                    "source": "pubmed",
                    "record_id": pmid,
                    "title": doc.get("title", ""),
                    "authors": [a.get("name", "") for a in doc.get("authors", [])[:3]],
                    "journal": doc.get("source", ""),
                    "published_date": pub_date,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "abstract_available": True,  # PubMed always has abstracts (or at least metadata)
                    "doi": next(
                        (a.get("value", "") for a in doc.get("articleids", []) if a.get("idtype") == "doi"),
                        None
                    ),
                })

            return {
                "source": "pubmed",
                "query_used": search_query,
                "total_results": total_results,
                "results": results,
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"PubMed search error: {e}")
            return {
                "source": "pubmed",
                "query_used": search_query,
                "error": str(e),
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }


# Singleton instance
pubmed_connector = PubMedConnector()
