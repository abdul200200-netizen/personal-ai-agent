# Implementation Merge Plan

## Objective

Create a clear, low-risk plan to merge the implementation work for the `personal-ai-agent` project into a stable, production-ready state without losing functional progress already present in the repository.

This plan is tailored to the current codebase structure:
- `app/main.py` for FastAPI application and routes
- `app/database.py` for persistence and task/memory management
- `app/config.py` for environment-driven configuration
- `app/services/` for provider integrations (Telegram, OpenCode, Google Workspace)
- `tests/` for validation and regression checks

---

## 1. Merge Scope

The merge should include the following implementation areas:

1. Core application runtime
   - FastAPI entrypoint and startup lifecycle
   - Health checks and API status routes
   - Conversation, task, memory, calendar, and sheet APIs

2. AI integration layer
   - OpenCode provider configuration and execution flow
   - Tool calling support for app features
   - Request/response orchestration and model fallback behaviour

3. External service integrations
   - Telegram webhook and polling support
   - Google Calendar + Google Sheets integration
   - Local SQLite fallback handling for incomplete external configuration

4. Persistence and state management
   - Conversation storage
   - Task tracking
   - Persistent memory records
   - Local DB schema consistency and migration coverage

5. Testing, validation, and documentation
   - API contract verification
   - Service-level smoke tests
   - Environment setup documentation and operational guidance

---

## 2. Current Repository Assessment

The repository already contains a strong base implementation with:

- A FastAPI app in `app/main.py`
- SQLite-backed persistence in `app/database.py`
- external integrations via `app/services/`
- environment configuration in `app/config.py`
- a Docker setup and Python dependency environment
- test scaffolding under `tests/`

This means the merge is best treated as a consolidation and stabilization pass rather than a greenfield rewrite.

Key principle: preserve working features while standardizing configuration, boundaries, and validation.

---

## 3. Merge Strategy

### Phase 1: Stabilize the foundation

Goal: ensure the app can start reliably in a controlled environment.

Tasks:
- Verify the required environment variables are clearly documented
- Confirm `app/config.py` is the single source of truth for runtime config
- Validate startup path of `uvicorn app.main:app`
- Confirm default/fallback behaviour when Google or Telegram credentials are absent
- Ensure SQLite initialization is deterministic and safe on first boot

Deliverables:
- clean startup behavior
- predictable defaults
- environment contract documented in `.env.example`

---

### Phase 2: Consolidate the app structure

Goal: reduce drift between modules and make feature ownership explicit.

Tasks:
- Map each API route to its implementation owner
- Confirm feature responsibilities:
  - HTTP/web routes in `app/main.py`
  - persistence in `app/database.py`
  - service adapters in `app/services/`
  - runtime config in `app/config.py`
- Remove duplicate logic or hidden assumptions between services
- Standardize naming patterns for tools, tasks, and memory records

Deliverables:
- clear module boundaries
- reduced cross-coupling
- easier debugging and future merges

---

### Phase 3: Merge service integrations

Goal: ensure Telegram, OpenCode, and Google integrations all behave consistently under the same runtime contract.

Tasks:
- Verify OpenCode calls are routed through a single abstraction layer
- Validate Telegram webhook vs polling mode behavior
- Confirm Google Workspace calls fail gracefully when credentials are missing
- Ensure integration-specific errors are surfaced without breaking the main app
- Add logging/telemetry around provider failures and retries

Deliverables:
- resilient provider integrations
- graceful degraded mode
- easier operational support

---

### Phase 4: Merge persistence and data integrity

Goal: lock in a consistent state model for users, conversations, tasks, and memories.

Tasks:
- Validate data access patterns in `app/database.py`
- Review schema initialization and update paths
- Define expected lifecycle for:
  - conversation sessions
  - tasks
  - memories
  - calendar/sheets records
- Add missing constraints or validation for required values
- Preserve backward compatibility for existing local SQLite data

Deliverables:
- deterministic storage contracts
- clear database boundaries
- safer local development and deployment experience

---

### Phase 5: Validation and regression safety

Goal: confirm the merged implementation is stable before release.

Tasks:
- Run the existing Python test suite under `tests/`
- Add smoke tests for:
  - app startup
  - health endpoint
  - conversation creation flow
  - task CRUD flow
  - memory persistence
  - external service fallback behavior
- Verify Docker startup and container assumptions
- Test one complete happy-path flow end-to-end

Deliverables:
- regression coverage
- release confidence
- reduced merge risk

---

## 4. Recommended Merge Order

The implementation should be merged in this order to minimize risk:

1. Configuration and startup
2. Database and persistence contracts
3. Core API routes
4. OpenCode orchestration
5. Telegram integration
6. Google Workspace integration
7. Testing and documentation cleanup

This order keeps the app runnable while enabling each service layer to be validated incrementally.

---

## 5. Risk Areas to Watch

### Configuration drift
- Different modules may assume different environment variable names or defaults.
- Mitigation: centralize config and validate at boot.

### SQLite state mismatches
- Long-lived data may be missing schema expectations after feature changes.
- Mitigation: add schema version checks and safe migration patterns.

### External service dependency failures
- Telegram and Google integrations can fail in production settings if credentials are absent or invalid.
- Mitigation: degrade gracefully and log clearly.

### Incomplete test coverage
- Feature additions may not be fully exercised by the current suite.
- Mitigation: add smoke tests around critical flows.

### Unclear module ownership
- Routes, service integrations, and persistence code may overlap.
- Mitigation: define a simple ownership map and keep each area narrowly scoped.

---

## 6. Acceptance Criteria

The merge can be considered complete when all of the following are true:

- The application starts cleanly with the documented environment setup
- Health and status endpoints work reliably
- Conversation, task, and memory flows persist correctly
- OpenCode integration works in the configured mode
- Telegram and Google integrations fail gracefully when not configured
- Local SQLite fallback works as expected
- Test coverage covers the critical happy paths
- Documentation is accurate and reflects the current implementation

---

## 7. Recommended Task Breakdown

### Sprint 1: Foundation merge
- environment config audit
- app startup validation
- DB schema verification
- core route smoke testing

### Sprint 2: Service merge
- OpenCode integration verification
- Telegram integration stabilization
- Google Workspace fallback validation

### Sprint 3: Hardening
- error handling and logging
- test additions
- release readiness doc and operational notes

---

## 8. Final Recommendation

The best path is not a broad rewrite. The repo already contains meaningful working functionality and a coherent architecture. The merge should therefore focus on:

- standardizing the runtime contract,
- tightening module boundaries,
- validating persistence and service fallback behaviors,
- and adding regression coverage before final release.

This is the safest way to merge the implementation into a stable, maintainable codebase.

---

## 9. Suggested Next Action

Before merging anything larger, perform a targeted validation pass:

1. Start the app locally with the sample configuration
2. Verify `/health` and `/api/status`
3. Test a sample conversation flow
4. Validate task persistence and memory persistence
5. Confirm graceful behavior without Google credentials
6. Run the tests and fix failures before continuing to additional feature merges

This creates a reliable baseline from which the rest of the implementation can safely merge.
