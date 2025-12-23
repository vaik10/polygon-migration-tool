# PolygonMigration — System Documentation

Version: project snapshot (December 2025)

This document explains how the system works, user flows, and the main data models. It is intended for developers and AI agents who need to work on or extend the project.

**Contents**
- 1. High-level architecture
- 2. User Flows
  - 2.1 User Login
  - 2.2 Fetch Problem from Polygon
  - 2.3 Migrate Problem to Database
  - 2.4 Migrate Test Cases to Database
  - 2.5 Migrate Test Cases to Cloud Storage (Azure)
- 3. Data Models
- 4. Redis and Cloud naming conventions
- 5. Notes, gotchas and troubleshooting

---

## 1. High-level architecture

- Framework: Django (project root `PolygonMigration/`).
- Apps of interest: `problems`, `users`, `contents`.
- External integrations:
  - Polygon API (https://polygon.codeforces.com/api/) — fetch problems, packages, test inputs/answers, solutions, and checker info.
  - Azure Blob Storage (via `azure.storage.blob`) — store test case files and compiled custom checkers.
  - Redis (optional) — caching of fetched test cases to reduce Polygon API calls.
- Core logic lives in `problems/polygon_api.py` and is orchestrated by `problems/views.py`.

---

## 2. User Flows

Each flow below describes the trigger, payload, processing steps, external API calls, database/storage operations, and UI result.

### 2.1 User Login

- Trigger: user posts login form at `/users/login/` (link used in `@user_passes_test` redirect). Only staff users may access the migration UI.
- Data sent to backend: `email`, `password` from the login form.
- Processing steps:
  1. `users.backends.EmailBackend` authenticates the user by looking up `User` by `email` (custom `AUTH_USER_MODEL`).
  2. Password check performed (Django's `check_password`).
  3. If successful, Django sets the session cookie and marks the user as authenticated.
  4. Staff-only protection: the `problems.index` view is decorated with `@user_passes_test(lambda u: u.is_authenticated and u.is_staff, ...)`. Non-staff users are redirected to `/users/login/`.
- External APIs: none.
- DB operations: select from `users_user` (email lookup), no modifications unless login hooks are present.
- User sees: login success → redirected to migration UI (or originally requested page); login failure → error message.

### 2.2 Fetch Problem from Polygon

- Trigger: In the migration UI (`problems/index.html`) user submits the form with the `problem_id` (Polygon ID) and clicks the fetch/preview action.
- Data sent: `problem_id` (Polygon ID). Optionally UI might request test cases or other flags.
- Processing steps (server-side `problems.views.index`):
  1. Instantiate `PolygonAPI()` (in `problems/polygon_api.py`) which reads `POLYGON_API_KEY` and `POLYGON_API_SECRET` from settings.
  2. Check local DB for an existing `Problem` with `polygon_id` (`Problem.objects.filter(polygon_id=polygon_id).first()`).
  3. Call Polygon API endpoints:
     - `problem.info` (`PolygonAPI.get_problem_info`) — general metadata (time limit, memory, name).
     - `problem.packages` and `problem.package` (via `download_and_extract_package`) — download the problem package ZIP to extract `problem.html`.
     - Parse `problem.html` using `parse_problem_html` (in `problems/views.py`) for: title, legend (statement), input/output sections, notes.
     - `problem.tests`, `problem.testInput`, `problem.testAnswer` (via `get_all_test_cases`) — fetch test indexes and then individual input/output pairs.
     - `problem.checker` to detect checker type; `problem.solutions` / `problem.viewSolution` if solution content is required.
  4. Cache test cases in Redis using `store_test_cases_in_redis` (key prefix `polygon_migration_test_cases_{polygon_id}`) with short expiry (default 0.5 hours).
  5. Compose `context['fetched_problem']` for template rendering; prepare truncated previews of test cases for UI.
- External APIs called:
  - Polygon API endpoints listed above (all proxied via `PolygonAPI._make_request` and `_make_plain_request`).
- DB/storage operations:
  - Read `problems_problem` for existing problem metadata and tags.
  - Read Redis (via `PolygonAPI.get_test_cases_from_redis`) and possibly write Redis via `store_test_cases_in_redis`.
  - No writes to relational DB unless user requests migration actions.
- User sees:
  - Preview of fetched problem: title, parsed statement HTML, input/output sections, notes, list of test cases (preview), and detected checker type.
  - If problem exists in DB, UI shows DB data (tags, difficulty) and the main solution if retrievable.

### 2.3 Migrate Problem to Database

- Trigger: In the migration UI, user selects difficulty (required) and optionally tags/new tag, then clicks `Migrate to Database` (form POST with `migrate_to_db` flag).
- Data sent: `problem_id`, `difficulty`, `tags` (list), `new_tag` (optional), and previously fetched problem content is used from context.
- Processing steps (see `problems.views.index`):
  1. Validate that `difficulty` is provided (error if missing).
  2. Reuse parsed problem content from the earlier fetch (or re-fetch if needed): fields: title, slug (via `slugify`), problem_statement (HTML), input_format, output_format, constraints, editorial, time_limit, memory_limit, checker_type (normalized), notes.
  3. Find existing `Problem` by `polygon_id`.
     - If found: update fields and `notes`, then `save()`.
     - If not found: create a new `Problem` record via `Problem.objects.create(...)`.
  4. Process tags: clear existing `extra_tags`, then for each tag name call `ProblemTag.objects.get_or_create(tag_name=...)` and attach to the `Problem` M2M field; also create and attach `new_tag` if provided.
  5. Update sample test cases: get sample tests from Redis or Polygon; update or create `SampleTestCase` rows (ordered by `order`).
  6. Save and return DB success message.
- External APIs called: possibly Polygon endpoints to refresh content (same as fetch step), but main work is DB writes.
- DB/storage operations:
  - `problems_problem`: create or update a row with many text fields (title, slug, difficulty, problem_statement, notes, time_limit, memory_limit, checker_type, test_case_count).
  - `problems_problemtag`: create tags via `get_or_create` as needed.
  - `problems_sampletestcase`: create or update sample test cases tied to `problem` FK.
  - M2M relationship stored through the implicit join table for `extra_tags`.
- User sees: success message in the UI (`context['db_success']`) and the problem displayed as stored in DB (`context['db_problem']`).

### 2.4 Migrate Test Cases to Database

- Trigger: user clicks `Migrate Test Cases to DB` in the UI (form POST with `migrate_test_cases_to_db` flag).
- Data sent: `problem_id`.
- Processing steps (server-side `problems.views.index`):
  1. Ensure the `Problem` exists in DB (error if missing — ask to migrate problem first).
  2. Retrieve test cases from Redis (`get_test_cases_from_redis`) or fallback to `get_all_test_cases` from Polygon and then `store_test_cases_in_redis`.
  3. For each test case (ordered by index):
     - Strip trailing whitespace, ensure both input and output exist.
     - Truncate `input` and `output` to the first 260 characters before storing (this implementation choice restrains DB payload size).
     - If a corresponding `ProblemTestCase` exists at the same order index: update fields; else create a new `ProblemTestCase` with `is_sample`, `input`, `output`, `description`, `order`.
  4. Return success message.
- External APIs called: Polygon only if Redis cache missing.
- DB/storage operations:
  - `problems_problemtestcase`: create or update rows for each test case referencing `problem` FK.
- User sees: success message in UI (`context['success']`), and test case previews in the page.

### 2.5 Migrate Test Cases to Cloud Storage (Azure)

- Trigger: user clicks `Migrate to Azure` in the UI (form POST with `migrate_to_azure` flag).
- Data sent: `problem_id`. Azure credentials are read from environment (`settings.AZURE_*`).
- Processing steps (server-side `problems.views.index` and `PolygonAPI.migrate_to_azure_blob`):
  1. Ensure `Problem` exists in DB (the code requires an existing DB Problem and will abort with a message if not present).
  2. Remove Redis cache keys related to this problem via `PolygonAPI.delete_problem_test_case_cache` (note: method deletes keys with a legacy prefix `oj_dev_with_redis_storage_test_cases_{db_problem_id}*` — see Gotchas).
  3. Detect if a custom checker exists using `api.get_custom_checker_info`.
  4. Call `api.migrate_to_azure_blob(...)` which:
     a. Attempts to get test cases from Redis (prefix `polygon_migration_test_cases_{polygon_id}`) using `get_test_cases_from_redis`.
     b. If missing, fetches all test cases from Polygon (`get_all_test_cases`) and stores them in Redis (`store_test_cases_in_redis`).
     c. Instantiates `AzureBlobManager(...)` with `account_url`, `tenant_id`, `client_id`, `client_secret` and obtains a `BlobServiceClient`.
     d. Calls `blob_manager.empty_blob(container_name, problem_id_for_naming)` to delete existing blobs under `test_cases/{problem_id}/`.
     e. Iterates test cases; for each test with both input and output calls `blob_manager.upload_test_case(container_name, db_problem_id, idx, input, output)`:
        - The input blob path: `test_cases/{db_problem_id}/{NN}` (NN zero-padded two digits).
        - The output blob path: `test_cases/{db_problem_id}/{NN}.a`.
     f. If a custom checker was detected: fetch source via `fetch_custom_checker_file`, attempt to compile with `compile_custom_checker` (uses `g++` and `CUSTOM_CHECKER_DIR`), and upload the compiled binary (or source `.cpp` as fallback) to blob `test_cases/{db_problem_id}/custom_checker` (or `custom_checker.exe` on Windows).
  5. Return success message (with custom checker status if applicable).
- External APIs called:
  - Azure Blob Storage via `azure.storage.blob.BlobServiceClient`.
  - Polygon if Redis missing.
- DB/storage operations:
  - Reads `problems_problem` to get the `db_problem_id` for naming.
  - Writes objects into Azure Blob container as described.
  - The app may clear Redis keys as a pre-step.
- User sees: success or error messages in the UI (`context['success']` / `context['error']`).

---

## 3. Data Models

Below are the primary Django models used by the migration app. Table names follow Django default `app_model` convention (e.g., `problems_problem`).

### 3.1 `ProblemTag` (table `problems_problemtag`)
- Fields:
  - `id` (AutoField, primary key)
  - `tag_name` (CharField, max_length=100, unique=True)
- Relationships: M2M with `Problem` via `Problem.extra_tags`.

### 3.2 `Problem` (table `problems_problem`)
- Fields (types in Django model terms):
  - `id` (AutoField, PK)
  - `polygon_id` (CharField(max_length=100), unique=True, blank=True)
  - `title` (CharField(255))
  - `slug` (SlugField(unique=True, max_length=255))
  - `difficulty` (CharField(max_length=10), choices: easy/medium/hard)
  - `avg_time_taken` (FloatField, default=30.0)
  - `total_submissions` (IntegerField, default=0)
  - `problem_statement` (TextField, blank=True, null=True)
  - `input_format` (TextField, blank=True, null=True)
  - `output_format` (TextField, blank=True, null=True)
  - `constraints` (TextField, blank=True, null=True)
  - `editorial` (TextField, blank=True, null=True)
  - `time_limit` (FloatField, default=1000, help_text milliseconds)
  - `problem_statement_url` (URLField, blank=True, null=True)
  - `additional_info` (TextField, blank=True, null=True)
  - `extra_tags` (ManyToManyField to `ProblemTag`, blank=True)
  - `memory_limit` (IntegerField, default=256)
  - `checker_type` (CharField(max_length=10), many choices incl. 'custom')
  - `test_case_count` (IntegerField, default=0)
  - `is_locked` (BooleanField, default=False)
  - `genie_assist` (BooleanField, default=False)
  - `genie_plus` (BooleanField, default=False)
  - `content_video_url` (URLField, blank=True, null=True)
  - `voice_assistant` (CharField(max_length=128), blank=True, null=True)
  - `notes` (TextField, blank=True, null=True)
  - `genie_chat` (BooleanField, default=False)
- Constraints:
  - `polygon_id` is marked unique.
  - `slug` is unique (used for human-friendly URLs).

### 3.3 `SampleTestCase` (table `problems_sampletestcase`)
- Fields:
  - `id` (AutoField, PK)
  - `problem` (ForeignKey to `Problem`, on_delete=CASCADE, related_name='sample_test_cases')
  - `input` (TextField)
  - `output` (TextField)
  - `order` (IntegerField, default=0)
- Notes: ordered by (`problem`, `order`) — used to present samples in problem statements.

### 3.4 `ProblemTestCase` (table `problems_problemtestcase`)
- Fields:
  - `id` (AutoField, PK)
  - `problem` (ForeignKey to `Problem`, on_delete=CASCADE, related_name='problem_test_cases')
  - `is_sample` (BooleanField, default=False)
  - `input` (TextField)
  - `output` (TextField)
  - `description` (TextField, blank=True, null=True)
  - `order` (IntegerField, default=0)
  - `created_at` (DateTimeField, auto_now_add=True)
  - `updated_at` (DateTimeField, auto_now=True)
- Notes: ordered by (`problem`, `order`). The view truncates input/output to 260 characters before storing.

### 3.5 `User` (table `users_user`)
- Fields (key ones shown):
  - `id` (AutoField, PK)
  - `username` (CharField(150), unique=True)
  - `first_name`, `last_name` (CharField)
  - `email` (EmailField, unique=True) — primary USERNAME_FIELD
  - `gender` (CharField choices), `contact_number`, `college`, `graduation_year`, `company`
  - `profile_picture` (URLField)
  - `primary_coding_language`, `linkedin_profile`, `codeforces_profile`, `leetcode_profile`
  - `on_going_topic` (ForeignKey to `contents.Topic`, on_delete=SET_NULL, nullable)
  - `is_premium`, `is_active`, `is_staff`, `is_superuser`
  - `discord_id` (unique, nullable)
- Constraints: `email` and `username` are unique. Custom user manager `UserManager` handles creation.

### 3.6 Other models (contents app)
- `contents.Topic` is referenced by `User.on_going_topic`; see `contents/models.py` for details if you need to document topics.

---

## 4. Redis and Cloud naming conventions

- Redis keys used by this app:
  - `polygon_migration_test_cases_{polygon_id}_count` — stores the number of test cases (setex with expiry default 0.5 hours).
  - `polygon_migration_test_cases_{polygon_id}_test_{i}` — stores JSON payloads for each test case.
- Note: There is also a legacy/alternate key prefix referenced in `PolygonAPI.delete_problem_test_case_cache`: `oj_dev_with_redis_storage_test_cases_{db_problem_id}*`. This mismatch is a potential gotcha.

- Azure Blob naming conventions (in `problems/AzureTestcase.py` and `polygon_api.py`):
  - Test case inputs: `test_cases/{db_problem_id}/{NN}`
  - Test case outputs: `test_cases/{db_problem_id}/{NN}.a`
  - Custom checker binary: `test_cases/{db_problem_id}/custom_checker` (or `custom_checker.exe` on Windows)
  - When clearing: list blobs with prefix `test_cases/{problem_id}/` and delete each entry.

---

## 5. Notes, gotchas and troubleshooting

- settings.py requires `POLYGON_API_KEY` and `POLYGON_API_SECRET` in `.env`. Startup raises if missing — for local testing set dummy values.
- Requirements filename is `requirement.txt` (singular) — CI and bootstrap scripts must use that name.
- Custom checker compilation requires `g++` available on PATH. `CUSTOM_CHECKER_DIR` env var can be used to persist compiled binaries; otherwise the code uses temporary directories.
- Redis prefix mismatch: storing uses `polygon_migration_test_cases_{polygon_id}` while `delete_problem_test_case_cache` uses `oj_dev_with_redis_storage_test_cases_{db_problem_id}` — this can leave stale caches or fail to clear cached test cases when expected. Consider normalizing these prefixes.
- `download_and_extract_package` expects a ZIP response from Polygon (starts with `PK`); if API returns an error page, the function raises.
- When modifying models (e.g., adding fields like `notes`) ensure migrations are created/applied (`makemigrations`, `migrate`). Missing migrations cause `UndefinedColumn` DB errors (see earlier stack traces).

If you want, I can:
- Generate a diagram of the flow (sequence diagram) or ER diagram for models.
- Add a short `docs/CONTRIBUTING.md` with setup commands and common troubleshooting steps.
- Normalize Redis key prefixes and add unit tests for the Polygon API wrapper using `responses` or `requests-mock`.

---

End of document.
