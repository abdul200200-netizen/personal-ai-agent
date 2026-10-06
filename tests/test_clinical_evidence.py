"""
Tests for clinical evidence search functionality.

Tests cover:
- PubMed connector (mocked API calls)
- Europe PMC connector (mocked API calls)
- ClinicalTrials.gov connector (mocked API calls)
- openFDA connector (mocked API calls)
- Clinical evidence search orchestration
- PHI detection and blocking
- Citation validation
- Policy engine guardrails
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Use temp database for tests
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["SQLITE_DB_PATH"] = _tmp_db.name

from app.main import app
from app.core.policy import policy_engine
from app.tools.clinical_evidence_search import search_clinical_evidence, search_drugs
from app.connectors.clinical.pubmed import pubmed_connector
from app.connectors.clinical.europepmc import europepmc_connector
from app.connectors.clinical.clinicaltrials import clinicaltrials_connector
from app.connectors.clinical.openfda import openfda_connector


client = TestClient(app)


# --------------------------------------------------------------------------
# PHI Detection Tests
# --------------------------------------------------------------------------

class TestPHIDetection:
    """Test PHI (patient-identifiable information) detection."""

    def test_phi_detected_with_patient_name(self):
        """Should detect patient name patterns."""
        text = "My patient John Smith has diabetes"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is True
        assert len(result["matches"]) > 0

    def test_phi_detected_with_national_id(self):
        """Should detect Saudi national ID numbers."""
        text = "Patient ID 1234567890 with hypertension"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is True

    def test_phi_detected_with_phone_number(self):
        """Should detect phone numbers."""
        text = "Call patient at 0501234567 about their condition"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is True

    def test_phi_detected_with_dob(self):
        """Should detect date of birth mentions."""
        text = "Patient DOB: 15/03/1985 presents with chest pain"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is True

    def test_phi_detected_with_mrn(self):
        """Should detect medical record numbers."""
        text = "MRN: 12345 admitted for surgery"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is True

    def test_phi_not_detected_for_general_question(self):
        """Should NOT flag general clinical questions."""
        text = "What is the evidence for SGLT2 inhibitors in heart failure?"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is False

    def test_phi_not_detected_for_drug_query(self):
        """Should NOT flag drug name queries."""
        text = "metformin side effects"
        result = policy_engine.check_phi(text)
        assert result["phi_detected"] is False

    def test_memory_write_blocks_phi(self):
        """Memory writes should be blocked if they contain PHI."""
        result = policy_engine.check_memory_write("patient_case", "John Smith, DOB 15/03/1985")
        assert result["allowed"] is False
        assert "patient-identifiable" in result["warning"].lower()

    def test_memory_write_blocks_credentials(self):
        """Memory writes should block credential storage."""
        result = policy_engine.check_memory_write("api_config", "password=secret123")
        assert result["allowed"] is False
        assert "password" in result["warning"].lower()

    def test_memory_write_allows_normal_data(self):
        """Normal memory writes should be allowed."""
        result = policy_engine.check_memory_write("preference", "prefers bullet points")
        assert result["allowed"] is True


# --------------------------------------------------------------------------
# PubMed Connector Tests (Mocked)
# --------------------------------------------------------------------------

class TestPubMedConnector:
    """Test PubMed E-utilities connector with mocked API calls."""

    @pytest.mark.asyncio
    async def test_pubmed_search_success(self):
        """Test successful PubMed search."""
        mock_esearch_response = {
            "esearchresult": {
                "count": "100",
                "idlist": ["12345678", "23456789"],
            }
        }
        mock_esummary_response = {
            "result": {
                "12345678": {
                    "title": "SGLT2 inhibitors in heart failure",
                    "source": "NEJM",
                    "pubdate": "2025 Mar",
                    "authors": [{"name": "Smith J"}, {"name": "Doe A"}],
                    "articleids": [{"idtype": "doi", "value": "10.1234/test"}],
                },
                "23456789": {
                    "title": "Meta-analysis of empagliflozin",
                    "source": "Lancet",
                    "pubdate": "2024 Dec",
                    "authors": [{"name": "Johnson R"}],
                    "articleids": [],
                },
                "uids": "12345678 23456789",
            }
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.side_effect = [
                AsyncMock(json=lambda: mock_esearch_response, raise_for_status=lambda: None),
                AsyncMock(json=lambda: mock_esummary_response, raise_for_status=lambda: None),
            ]

            result = await pubmed_connector.search("SGLT2 inhibitors heart failure", max_results=2)

            assert result["source"] == "pubmed"
            assert result["total_results"] == 100
            assert len(result["results"]) == 2
            assert result["results"][0]["record_id"] == "12345678"
            assert "SGLT2" in result["results"][0]["title"]
            assert "pubmed.ncbi.nlm.nih.gov" in result["results"][0]["url"]

    @pytest.mark.asyncio
    async def test_pubmed_search_with_date_filter(self):
        """Test PubMed search with date range filter."""
        mock_response = {
            "esearchresult": {"count": "0", "idlist": []},
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = AsyncMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await pubmed_connector.search(
                "diabetes",
                date_from="2024/01/01",
                date_to="2025/12/31",
            )

            assert result["source"] == "pubmed"
            assert result["total_results"] == 0
            assert len(result["results"]) == 0

    @pytest.mark.asyncio
    async def test_pubmed_search_error_handling(self):
        """Test PubMed search error handling."""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.side_effect = Exception("API timeout")

            result = await pubmed_connector.search("test query")

            assert result["source"] == "pubmed"
            assert "error" in result
            assert len(result["results"]) == 0


# --------------------------------------------------------------------------
# Europe PMC Connector Tests (Mocked)
# --------------------------------------------------------------------------

class TestEuropePMCConnector:
    """Test Europe PMC connector with mocked API calls."""

    @pytest.mark.asyncio
    async def test_europepmc_search_success(self):
        """Test successful Europe PMC search."""
        mock_response = {
            "hitCount": 50,
            "resultList": {
                "result": [
                    {
                        "pmcid": "PMC1234567",
                        "pmid": "98765432",
                        "title": "Open access trial results",
                        "journalTitle": "BMJ Open",
                        "pubYear": "2025",
                        "isOpenAccess": "Y",
                        "abstractText": "Study abstract...",
                        "authorString": "Author A, Author B",
                    }
                ]
            },
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = AsyncMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await europepmc_connector.search("clinical trial", max_results=1)

            assert result["source"] == "europe_pmc"
            assert result["total_results"] == 50
            assert len(result["results"]) == 1
            assert result["results"][0]["pmcid"] == "PMC1234567"
            assert result["results"][0]["full_text_available"] is True

    @pytest.mark.asyncio
    async def test_europepmc_open_access_filter(self):
        """Test Europe PMC open-access filter."""
        mock_response = {"hitCount": 0, "resultList": {"result": []}}

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = AsyncMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await europepmc_connector.search("diabetes", open_access_only=True)

            # Verify query includes open-access filter
            call_args = mock_get.call_args
            assert "OPEN_ACCESS:y" in call_args[1]["params"]["query"]


# --------------------------------------------------------------------------
# ClinicalTrials.gov Connector Tests (Mocked)
# --------------------------------------------------------------------------

class TestClinicalTrialsConnector:
    """Test ClinicalTrials.gov connector with mocked API calls."""

    @pytest.mark.asyncio
    async def test_clinicaltrials_search_success(self):
        """Test successful ClinicalTrials.gov search."""
        mock_response = {
            "totalCount": 25,
            "studies": [
                {
                    "protocolSection": {
                        "identificationModule": {
                            "nctId": "NCT12345678",
                            "briefTitle": "Empagliflozin Trial",
                            "officialTitle": "Phase 3 Trial of Empagliflozin",
                        },
                        "statusModule": {
                            "overallStatus": "RECRUITING",
                            "startDateStruct": {"date": "2024-01-15"},
                        },
                        "designModule": {
                            "studyType": "INTERVENTIONAL",
                            "phases": ["PHASE3"],
                            "enrollmentInfo": {"count": 500},
                        },
                        "conditionsModule": {
                            "conditions": ["Heart Failure", "Diabetes"],
                        },
                        "armsInterventionsModule": {
                            "interventions": [{"name": "Empagliflozin"}],
                        },
                        "descriptionModule": {},
                    }
                }
            ],
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = AsyncMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await clinicaltrials_connector.search("heart failure", max_results=1)

            assert result["source"] == "clinicaltrials"
            assert result["total_results"] == 25
            assert len(result["results"]) == 1
            assert result["results"][0]["record_id"] == "NCT12345678"
            assert result["results"][0]["registry_data"] is True
            assert result["results"][0]["status"] == "RECRUITING"


# --------------------------------------------------------------------------
# openFDA Connector Tests (Mocked)
# --------------------------------------------------------------------------

class TestOpenFDAConnector:
    """Test openFDA connector with mocked API calls."""

    @pytest.mark.asyncio
    async def test_openfda_drug_labels_success(self):
        """Test successful openFDA drug label search."""
        mock_response = {
            "meta": {"results": {"total": 5}},
            "results": [
                {
                    "id": "abc123",
                    "openfda": {
                        "brand_name": ["Metformin"],
                        "generic_name": ["metformin hydrochloride"],
                        "manufacturer_name": ["Generic Pharma Co"],
                        "route": ["ORAL"],
                    },
                    "indications_and_usage": ["Treatment of type 2 diabetes"],
                    "warnings": ["Lactic acidosis risk"],
                }
            ],
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = AsyncMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await openfda_connector.search_drug_labels("metformin", max_results=1)

            assert result["source"] == "openfda"
            assert len(result["results"]) == 1
            assert result["results"][0]["brand_name"] == "Metformin"
            assert result["results"][0]["regulatory_data"] is True


# --------------------------------------------------------------------------
# Clinical Evidence Search Orchestration Tests
# --------------------------------------------------------------------------

class TestClinicalEvidenceSearch:
    """Test clinical evidence search orchestration."""

    @pytest.mark.asyncio
    async def test_search_blocks_phi(self):
        """Search should block queries with PHI."""
        result = await search_clinical_evidence(
            question="My patient John Smith, DOB 15/03/1985, has diabetes"
        )

        assert "error" in result
        assert "patient-identifiable" in result["error"].lower()
        assert len(result["results"]) == 0

    @pytest.mark.asyncio
    async def test_search_all_sources(self):
        """Search should query multiple sources and consolidate results."""
        # Mock all connectors
        with patch.object(pubmed_connector, "search", new_callable=AsyncMock) as mock_pubmed, \
             patch.object(europepmc_connector, "search", new_callable=AsyncMock) as mock_europepmc, \
             patch.object(clinicaltrials_connector, "search", new_callable=AsyncMock) as mock_trials:

            mock_pubmed.return_value = {
                "source": "pubmed",
                "total_results": 10,
                "results": [{"record_id": "12345", "title": "Test", "url": "https://test", "published_date": "2025"}],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }
            mock_europepmc.return_value = {
                "source": "europe_pmc",
                "total_results": 5,
                "results": [{"record_id": "PMC67890", "title": "Test 2", "url": "https://test2", "published_date": "2024"}],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }
            mock_trials.return_value = {
                "source": "clinicaltrials",
                "total_results": 3,
                "results": [{"record_id": "NCT111", "title": "Trial", "url": "https://test3"}],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

            result = await search_clinical_evidence(
                question="SGLT2 inhibitors heart failure",
                sources=["pubmed", "europe_pmc", "clinicaltrials"],
            )

            assert result["sources_searched"] == ["pubmed", "europe_pmc", "clinicaltrials"]
            assert len(result["results"]) == 3
            assert result["total_results"] == 18  # 10 + 5 + 3
            assert "disclaimer" in result

    @pytest.mark.asyncio
    async def test_search_with_pico(self):
        """Search should support PICO framing."""
        with patch.object(pubmed_connector, "search", new_callable=AsyncMock) as mock_pubmed:
            mock_pubmed.return_value = {
                "source": "pubmed",
                "total_results": 1,
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

            await search_clinical_evidence(
                question="treatment effectiveness",
                population="adults with type 2 diabetes",
                intervention="metformin",
                comparator="placebo",
                outcomes=["HbA1c reduction", "weight loss"],
                sources=["pubmed"],
            )

            # Verify query includes PICO elements
            call_args = mock_pubmed.call_args
            query = call_args[1]["query"]
            assert "metformin" in query
            assert "diabetes" in query


# --------------------------------------------------------------------------
# API Endpoint Tests
# --------------------------------------------------------------------------

class TestClinicalEvidenceEndpoints:
    """Test clinical evidence API endpoints."""

    def test_evidence_sources_endpoint(self):
        """Test /api/evidence/sources endpoint."""
        res = client.get("/api/evidence/sources")
        assert res.status_code == 200
        data = res.json()
        assert "sources" in data
        assert "pubmed" in data["sources"]
        assert "europe_pmc" in data["sources"]
        assert "clinicaltrials" in data["sources"]
        assert "openfda" in data["sources"]

    def test_evidence_search_endpoint_blocks_phi(self):
        """Test /api/evidence/search blocks PHI."""
        res = client.post(
            "/api/evidence/search",
            json={"question": "Patient John Smith, DOB 15/03/1985"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "error" in data
        assert "patient-identifiable" in data["error"].lower()

    def test_drugs_endpoint(self):
        """Test /api/evidence/drugs endpoint."""
        with patch.object(openfda_connector, "search_drug_labels", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {
                "source": "openfda",
                "results": [],
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }

            res = client.post(
                "/api/evidence/drugs",
                json={"drug_name": "metformin"},
            )
            assert res.status_code == 200
            data = res.json()
            assert "labels" in data


# --------------------------------------------------------------------------
# Citation Validation Tests
# --------------------------------------------------------------------------

class TestCitationValidation:
    """Test citation validation."""

    def test_validate_complete_citations(self):
        """Should validate complete citations."""
        results = [
            {
                "source": "pubmed",
                "record_id": "12345",
                "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                "published_date": "2025-03-15",
            }
        ]
        result = policy_engine.validate_citations(results)
        assert result["valid"] is True
        assert len(result["issues"]) == 0

    def test_validate_missing_fields(self):
        """Should detect missing citation fields."""
        results = [
            {
                "source": "pubmed",
                # missing record_id and url
            }
        ]
        result = policy_engine.validate_citations(results)
        assert result["valid"] is False
        assert len(result["issues"]) > 0
