"""
Europe PMC REST API connector for retrieving literature and open-access full text.

API docs: https://europepmc.org/RestfulWebService
Rate limits: No verified public limit; using conservative 5 req/s
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class EuropePMCConnector:
    """Europe PMC REST API connector for clinical literature search."""

    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        open_access_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Search Europe PMC for articles matching the query.

        Args:
            query: Search query (Europe PMC syntax)
            max_results: Maximum number of results (default 10)
            date_from: Start date filter (YYYY-MM-DD)
            date_to: End date filter (YYYY-MM-DD)
            open_access_only: Filter for open-access articles only

        Returns:
            Dict with search results including PMIDs/PMCIDs, titles, dates, URLs
        """
        # Build search query with filters
        search_query = query

        # Add date range filter
        if date_from or date_to:
            date_filter = "FIRST_PDATE:["
            if date_from:
                date_filter += date_from
            else:
                date_filter += "1900-01-01"
            date_filter += " TO "
            if date_to:
                date_filter += date_to
            else:
                date_filter += "3000-12-31"
            date_filter += "]"
            search_query = f"{search_query} AND {date_filter}"

        # Add open-access filter
        if open_access_only:
            search_query = f"{search_query} AND OPEN_ACCESS:y"

        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search",
                    params={
                        "query": search_query,
                        "resultType": "core",
                        "format": "json",
                        "pageSize": max_results,
                        "sort": "RELEVANCE",
                    },
                )
                response.raise_for_status()
                data = response.json()

            result_list = data.get("resultList", {}).get("result", [])
            hit_count = data.get("hitCount", 0)

            results = []
            for article in result_list:
                pmid = article.get("pmid")
                pmcid = article.get("pmcid")
                doi = article.get("doi")

                # Determine best identifier and URL
                if pmcid:
                    record_id = pmcid
                    url = f"https://europepmc.org/article/PMC/{pmcid.replace('PMC', '')}"
                elif pmid:
                    record_id = pmid
                    url = f"https://europepmc.org/article/MED/{pmid}"
                elif doi:
                    record_id = doi
                    url = f"https://doi.org/{doi}"
                else:
                    continue

                # Extract publication date
                pub_date = article.get("pubYear", "")
                if not pub_date and article.get("firstPublicationDate"):
                    pub_date = article["firstPublicationDate"]

                results.append({
                    "source": "europe_pmc",
                    "record_id": record_id,
                    "pmid": pmid,
                    "pmcid": pmcid,
                    "title": article.get("title", ""),
                    "authors": [article.get("authorString", "")] if article.get("authorString") else [],
                    "journal": article.get("journalTitle", ""),
                    "published_date": pub_date,
                    "url": url,
                    "abstract_available": bool(article.get("abstractText")),
                    "abstract": article.get("abstractText", ""),
                    "full_text_available": bool(article.get("isOpenAccess") and article["isOpenAccess"] == "Y"),
                    "doi": doi,
                })

            return {
                "source": "europe_pmc",
                "query_used": search_query,
                "total_results": hit_count,
                "results": results,
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Europe PMC search error: {e}")
            return {
                "source": "europe_pmc",
                "query_used": search_query,
                "error": str(e),
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }


# Singleton instance
europepmc_connector = EuropePMCConnector()
