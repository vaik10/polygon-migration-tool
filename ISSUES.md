# Issues Analysis

This file was generated from `ISSUES_TEMPLATE.md` and contains issues discovered in the codebase.

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 1 | 2 | 0 | 1 | 4 |
| Code Issues | 0 | 2 | 2 | 3 | 7 |

---

## Product Issues

### [P1] Missing / Out-of-sync migrations causing Runtime DB errors

**Severity**: Critical

**Location**: Runtime (views.py triggers ORM queries) — e.g. `problems/views.py: line ~145` when calling `Problem.objects.filter(...)`

**Description**:
The codebase contains model changes (for example, `Problem.notes` was added to `problems.models.Problem`) but the database schema may not always be migrated. This causes `django.db.utils.ProgrammingError: column problems_problem.notes does not exist` when the ORM tries to SELECT that field.

**Impact**:
- Application crashes at runtime (500) on pages that query the updated model.
- Blocks normal operation of migration UI.

**Suggested Fix**:
- Ensure all model changes are committed with migrations checked into the repository.
- Run `python manage.py makemigrations` and `python manage.py migrate` in deployment scripts/CI where appropriate.
- Add a developer checklist / CI job that validates migrations are up-to-date (e.g., `python manage.py makemigrations --check`).

---

### [P2] Long-running external I/O inside DB transaction block

**Severity**: High

**Location**: `problems/views.py` — the `index` view wraps Azure uploads and Polygon fetches inside `with transaction.atomic():`.

**Description**:
The view performs network I/O (Polygon API calls, Azure uploads, compilation of checkers) while inside a database transaction. This can hold DB locks for long periods and increase the risk of transaction timeouts or contention.

**Impact**:
- Database connections/locks held for long durations, causing performance degradation and possible deadlocks/timeouts in production.
- External failures will trigger DB rollbacks but may leave external side effects (e.g., uploaded blobs) unless compensating logic is complete.

**Suggested Fix**:
- Move long-running external operations outside of the transactional block or use two-phase approach: commit DB changes first (or persist minimal DB state), then perform external operations and handle compensations explicitly.
- Add idempotency keys / atomic ops to allow safe retries.

---

### [P3] Test case truncation when saving to DB

**Severity**: High

**Location**: `problems/views.py` — in test case migration logic when creating `ProblemTestCase` (truncates to 260 chars)

**Description**:
When migrating test cases to the database the code truncates `input` and `output` to 260 characters before saving.

**Impact**:
- Data loss for long test cases (large inputs/outputs) which can make DB-stored test cases unusable for reproducing runs or debugging.
- Cloud storage still stores full test cases (if migrating to Azure), so DB and cloud may diverge.

**Suggested Fix**:
- Consider storing full test cases in DB (if acceptable) or store a pointer to the canonical blob in Azure/Redis and keep only summarized previews in DB.
- Document the truncation clearly in the UI and make it configurable.

---

### [P4] Mismatched button label vs action in migration UI

**Severity**: Low

**Location**: `problems/templates/problems/index.html` (approx line 314)

**Description**:
One of the form buttons in the migration UI has a label that does not accurately describe the action it performs. For example, the label reads "Migrate Description to DB" while the underlying form control triggers a different server action (migrating test cases to the database).

**Impact**:
- Confusion and reduced trust in the UI; increased support burden.
- Possibility of accidental overwrites or uploads if the wrong action is destructive.

**Suggested Fix**:
- Update the template so the button text, tooltip (`title`), and ARIA labels clearly describe the action (e.g., "Migrate Test Cases to DB").

---

## Code Issues

### [C1] Missing timeouts and retry logic on HTTP calls

**Severity**: High

**Location**: `problems/polygon_api.py::_make_request` — uses `requests.post(...)` without `timeout` or retry handling.

**Description**:
The code makes network requests to Polygon (and in other places) without specifying timeouts or retry/backoff. Long or hung requests can block workers and degrade service.

**Impact**:

**Suggested Fix**:

---
### [C2] Dynamic imports and `sys.path.append('.')` usage

**Severity**: Medium

**Location**: `problems/polygon_api.py` (multiple functions append to `sys.path` and import `problems.AzureTestcase` dynamically).

**Description**:
The code appends `.` to `sys.path` and imports `AzureTestcase` inside functions. This pattern is fragile, can hide import errors, and defeats static analysis.

**Impact**:
- Harder to reason about dependencies and import-time failures.
- Potential for inconsistent module resolution in different environments.

**Suggested Fix**:
- Import `AzureTestcase` at module top-level (or use Django's `apps.get_model` pattern if truly dynamic).
- Remove `sys.path.append('.')` and fix PYTHONPATH via proper packaging or Django settings.
---

### [C3] Broad exception handling and inconsistent error propagation

**Severity**: Medium

**Location**: Multiple files, e.g., `problems/polygon_api.py` and `problems/views.py`.

**Description**:
The code often catches generic `Exception` and returns empty defaults or swallows details (e.g., returning `[]` for failures to fetch tests). While this prevents crashes, it makes debugging and error recovery harder.

**Impact**:
- Real errors may be masked; callers may proceed with invalid data.
- Harder to surface meaningful errors to users or logs.

**Suggested Fix**:
- Catch specific exceptions where possible and propagate meaningful errors. Add structured logging with exception info. Use helper exceptions for expected failure modes.

---

### [C4] Hard-coded two-digit zero padding for test blob names

**Severity**: Low

**Location**: `problems/AzureTestcase.py` and `problems/polygon_api.py` — uses `f"{test_number:02d}"` when naming blobs.

**Description**:
Two-digit zero padding (`02d`) will cause naming collisions or inconsistent ordering for problems with >= 100 tests.

**Impact**:
- Problems with >=100 tests will have ambiguous blob names and could overwrite earlier blobs.

**Suggested Fix**:
- Use dynamic padding width (e.g., compute width based on test count: `width = len(str(total_tests))` and format accordingly) or use fixed wider padding (e.g., 04d).

---

### [C5] Unused imports and minor code quality issues

**Severity**: Low

**Location**: e.g., `problems/views.py` (`from bs4 import BeautifulSoup`, `from django.core.cache import cache`) and other modules.

**Description**:
There are a few imports that are unused; also some logging messages and comments could be tightened.

**Impact**:
- Reduced code clarity and slightly larger memory footprint.

**Suggested Fix**:
- Run a linter (e.g., `ruff`, `flake8`) and fix/clean unused imports and style issues.

---

### [C6] Redis key-prefix mismatch prevents cache invalidation

**Severity**: High

**Location**: `problems/polygon_api.py` — `store_test_cases_in_redis` vs `delete_problem_test_case_cache`

**Description**:
The code stores test cases using the prefix `polygon_migration_test_cases_{polygon_id}` (`store_test_cases_in_redis`) but `delete_problem_test_case_cache` removes keys matching `oj_dev_with_redis_storage_test_cases_{db_problem_id}*`. These two prefixes do not match and therefore clearing cache may be ineffective.

**Impact**:
- Cached test cases may remain stale and cause inconsistent migrations or upload of outdated data to Azure.
- `delete_problem_test_case_cache` will likely not remove the cached keys created by `store_test_cases_in_redis`.

**Suggested Fix**:
- Normalize the Redis prefix to a single constant (e.g., `POLY_TESTCASE_PREFIX`) and use it across all functions.
- Add unit tests asserting caching keys are created and removed.
---

### [C7] Non-standard requirements file name

**Severity**: Low

**Location**: Repository root — `requirement.txt` (singular) noted in README and used by docs.

**Description**:
The repository uses `requirement.txt` rather than the conventional `requirements.txt`. This can confuse developers and automation tools that expect the plural name.

**Impact**:
- Minor tooling friction (CI, onboarding scripts) if scripts assume `requirements.txt`.

**Suggested Fix**:
- Rename to `requirements.txt` or add instructions/aliases so tooling is aware of the file name.

---

## Edge Case Analysis

### Question 1: Empty Sample Test Cases

If a Polygon problem has 0 sample test cases but 15 regular test cases:

- Code path: When migrating problem to DB the view filters sample tests (`sample_tests = [test for test in test_cases if test.get('is_sample', False)]`) and then updates/creates `SampleTestCase` entries. With zero samples, that loop is skipped and any existing sample cases previously stored remain unchanged only if the code does not explicitly clear them.
- Database state: If previously there were sample cases, they are not deleted by the current code — code only updates or creates sample cases based on current sample list. This may leave stale sample rows.
- Re-migration: Re-migrating with zero samples will not remove existing sample entries; those stale samples remain unless manually cleared.
- Correctness: Not ideal — expected behavior on re-migration would be to reconcile (delete obsolete samples). Suggested to clear existing sample cases and recreate based on current data.

**Code References**:
- `problems/views.py` — sample test handling section.

### Question 2: Test Case Count Reduction

If a problem was migrated with 20 test cases and later Polygon reduces tests to 12, on re-migration:

- Database state: The code updates or creates `ProblemTestCase` entries by index; it does not delete extra test cases beyond the new count. So the DB may still contain the old 20 entries (the code updates the first 12 and leaves the remaining 8 unchanged), causing stale records.
- Cloud storage: `migrate_to_azure_blob` calls `blob_manager.empty_blob(container_name, problem_id_for_naming)` which deletes existing blobs for the problem before uploading the new set — so cloud storage will reflect the new reduced set correctly.
- Consistency: DB and cloud may diverge: DB may keep old test rows while cloud contains the new (smaller) set. This inconsistency can cause confusion.

**Suggested Fix**:
- When re-migrating, delete any DB `ProblemTestCase` entries beyond the new length or replace by truncating to match the current test count (and/or store canonical test count on `Problem`). Ensure both DB and cloud are reconciled.

**Code References**:
- `problems/views.py` — migration logic for `ProblemTestCase` and Azure upload.

### Question 3: Duplicate Problem Titles

If two Polygon problems share the same title (e.g., "Two Sum"):

- Which field causes the issue: `slug` is generated from title (`slugify(title)`) and is unique on the `Problem` model (``SlugField(unique=True)``).
- When error occurs: On create, if a generated `slug` already exists for a different problem, the `Problem.objects.create(...)` will raise an IntegrityError due to unique constraint on slug (or the save will fail).
- User experience: The user will see a DB error or migration failure message. 
- Is first problem affected?: The previously created problem is not affected but the second migration fails.
- Suggested fix: Use a slug generation strategy that includes the `polygon_id` or ensure uniqueness by appending a suffix when collisions occur (e.g., `slug = f"{slugify(title)}-{polygon_id}"`).

**Code References**:
- `problems/views.py` — slug creation: `slug = slugify(title)` and `Problem.slug` unique constraint in `problems/models.py`.

### Question 4: Data Truncation

When test cases are saved to DB the code truncates input/output to 260 chars (see `problems/views.py`).

- Where truncation happens: In the loop that migrates test cases to DB: `truncated_input = input_data[:260]` and `truncated_output = output_data[:260]`.
- Limit: 260 characters (not bytes) — may cut multi-byte characters.
- Problems affected: Problems with long inputs/outputs (e.g., large generated testcases) will lose fidelity in DB.
- DB vs cloud: The Azure migration stores full testcases; DB stores truncated copies. This leads to inconsistency if developers rely on DB testcases to reproduce runs.

**Code References**:
- `problems/views.py:538,539` - truncated_input = input_data[:260]
                                truncated_output = output_data[:260]


**Suggested Fix**:
- Store full testcases either in DB (if acceptable) or store only references/paths to blobs in Azure and keep previews in DB. Make truncation configurable and documented.

---