# Clinical Evidence Policy

## Allowed Sources

| Source | Purpose | Free Tier | Rate Limit | Auth Required |
|--------|---------|-----------|------------|---------------|
| PubMed E-utilities | Peer-reviewed literature | Yes | 3 req/s (10 req/s with NCBI key) | Optional (NCBI_API_KEY) |
| Europe PMC REST | Complementary records + open-access full text | Yes | No verified public limit | No |
| ClinicalTrials.gov API v2 | Trial status and results (registry data) | Yes | Generous (no documented hard limit) | No |
| MeSH RDF | Terminology expansion / synonym lookup | Yes | Reasonable use | No |
| UMLS | Terminology (requires license) | With key | 20 req/s | UMLS_API_KEY |
| openFDA | Drug labeling, adverse events, recalls | Yes | 240/min, 120k/day (with key) | Optional (OPENFDA_API_KEY) |
| web_search | Current public guidance not covered above | Varies | Varies | Varies |

## Forbidden Sources
- **UpToDate** — requires licensed contract (UpToDate Connect)
- **DynaMed** — requires DynaMed Decisions API license
- **OpenEvidence** — no public API
- **Trip Database** — no public API for bulk search
- Do NOT scrape any of these. If user asks for content from these, explain they require 
  licensed access and suggest the free alternatives above.

## Citation Format

Every key clinical claim MUST include:

```
[source] | [identifier] | [published/updated date] | [retrieved date] | [URL]
```

Example:
```
PubMed | PMID:12345678 | 2025-03-15 | 2026-10-06 | https://pubmed.ncbi.nlm.nih.gov/12345678/
```

### Evidence Labels
- **Peer-reviewed**: PubMed, Europe PMC (journal articles)
- **Registry data**: ClinicalTrials.gov (NOT peer-reviewed evidence)
- **Regulatory data**: openFDA (labeling, NOT comparative efficacy)
- **Terminology**: MeSH, UMLS (for search expansion, NOT evidence)

## Search Protocol

1. **Frame the question** as PICO where applicable (Population, Intervention, Comparator, Outcome)
2. **Clarify** population or jurisdiction ONLY if it materially changes the answer
3. **Expand terms** with MeSH synonyms before searching
4. **Search in order**: PubMed → Europe PMC → ClinicalTrials.gov → MeSH → openFDA
5. **Apply per-source rate limits** with exponential backoff on 429/5xx
6. **Cache terminology** lookups for 12-24 hours
7. **Report limitations**: if only abstracts available, say so; if sources conflict, show disagreement

## Rate Limits and Caching

| Source | Limit | Strategy |
|--------|-------|----------|
| PubMed (no key) | 3 req/s | Throttle + cache 24h |
| PubMed (with key) | 10 req/s | Throttle + cache 24h |
| Europe PMC | No verified limit | Conservative: 5 req/s + cache 24h |
| ClinicalTrials.gov | No hard limit | Conservative: 5 req/s + cache 12h |
| MeSH RDF | Reasonable use | Cache 24h |
| UMLS | 20 req/s | Cache 12h |
| openFDA (no key) | 120/min | Throttle + cache 12h |
| openFDA (with key) | 240/min, 120k/day | Throttle + cache 12h |

**Cache TTL defaults**: 24h for literature, 12h for trials/drug data. 
Configurable via `CLINICAL_EVIDENCE_CACHE_TTL` env var.

## PHI Policy

- **NEVER** send patient-identifiable information to any external search service
- **NEVER** store patient cases, identifiers, or clinical details in:
  - USER.md (user profile)
  - MEMORY.md (durable memory)
  - Episodic memory / conversation history
  - Cron job output or scheduled briefs
  - Log files
- If a query contains potential PHI:
  1. Ask the user to de-identify before proceeding
  2. Do NOT attempt auto-redaction as the sole safeguard
  3. Refuse to process until de-identified

## Uncertainty and Limitations

Every evidence response must include:
- **Search date and time** (when the search was performed)
- **Sources searched** (which APIs were queried)
- **Limitations** (e.g., "only abstracts available", "no meta-analyses found", 
  "limited to English-language results")
- **Conflicts** (if sources disagree, show the disagreement — don't hide it)
- **Retrieval date** (distinct from publication date)

Never claim a complete systematic review from abstract-level data alone.
