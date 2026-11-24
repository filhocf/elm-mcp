---
name: elm-qm
description: Read and write IBM ELM ETM (Engineering Test Management, QM) test resources through elm-mcp: list and read the 14 resource types, see which requirements a test case validates, read test results, and create, update, delete and link test cases, test plans, test suites and test scripts. Use when working with test cases, test coverage or test results in an ETM project area.
metadata:
  version: "0.2.0"
  keywords: "qm, etm, test, testcase, test case, testplan, testsuite, testscript, test result, coverage, verdict, validates requirement, tests change request, elm"
---

# ELM QM — ETM test resources

## Purpose

Work with test artifacts in an IBM ELM **ETM** (QM) project area: read test cases and the other
resource types, check coverage and results, and write test cases, plans, suites and scripts **by title**.
Covers `search_testcases`, `list_qm_resources`, `get_qm_resource`, `get_test_coverage`,
`get_test_results`, `create_qm_resource`, `update_qm_resource`, `delete_qm_resource`, `link_test_case`,
`unlink_test_case`. Domain is `qm`: `list_projects(domain="qm")` gives the project names.

ETM answers **only RDF/XML** (JSON is refused with 406). The tools handle that; you never build XML.

## When to use

- Find test cases, see what a test case validates (requirements) or tests (work items).
- Report coverage: which test cases cover which requirements or work items.
- Read test results: verdict, points per status, and the execution record they came from.
- Create or edit a test case, plan, suite or script (title and description), or delete one.
- Link a test case to a requirement or to a work item. For links starting from a **work item**, or for
  the whole picture, see the **`elm-traceability`** skill.

## Read

1. `search_testcases(project, query=None)` -> `{"items": [{uri, title, identifier, short_id, state, type}], "truncated"}` (up to 1000 items).
2. `list_qm_resources(project, type, query=None, limit=None)` lists any of the 14 types (case-insensitive):
   `TestPlan`, `TestCase`, `TestScript`, `TestScriptStep`, `TestSuite`, `TestExecutionRecord`,
   `TestSuiteExecutionRecord`, `TestResult`, `TestSuiteResult`, `TestData`, `Keyword`, `TestEnvironment`,
   `BuildRecord`, `BuildDefinition`. Returns `{"items", "truncated", "total"}`; default 50, maximum 1000.
3. `get_qm_resource(uri)` -> normalized properties plus `links` grouped by kind: `validates_requirement`,
   `tests_change_request`, `uses_test_script`, `runs_test_case`, `executes_test_script`, `test_results`,
   `reports_on_test_case`, `produced_by_execution_record`.
4. `get_test_coverage(project)` -> per test case, the requirements it validates and the work items it
   tests: `{"items": [{uri, title, short_id, requirements, work_items}], "truncated"}`.
5. `get_test_results(project, test_case=None)` -> `{"items": [...], "truncated"}`; each item is `{uri,
   title, verdict, verdict_name, points: {passed, failed, blocked}, execution_record, test_case}`.
   `verdict` is the URI of the verdict
   resource and `verdict_name` its readable end (`inconclusive`, ...). Pass a TestCase URI to filter.

## Write (by title and description only)

- `create_qm_resource(project, type, title, description=None, if_exists="error")` creates a `TestCase`,
  `TestPlan`, `TestSuite` or `TestScript`. The factory is chosen by resource type, never by title or
  position. Limits: title 255 characters, description 32768; control characters are dropped.
  `if_exists`: `error` (default) refuses a same-type resource with the same normalized title and names it,
  `return` hands it back with `created: false`, `create` skips the check. A timeout never resends the POST.
- `update_qm_resource(uri, title=None, description=None)` re-reads the resource, changes only what you
  pass and PUTs it back with `If-Match`; everything else the server sent is kept byte for byte. A 412 is
  a `[conflito]` error (re-read and retry yourself).
- `delete_qm_resource(uri)` is **irreversible** and unconditional; it is confirmed by re-reading
  (404/410). Ask the user first.
- `link_test_case(test_case, target, link_type)` / `unlink_test_case(...)`: `link_type` is
  `validatesRequirement` (target = an RM requirement URI) or `testsChangeRequest` (target = a CCM work
  item URI). Same server only. An already linked target is `changed: false` and nothing is written.

## Notes / Gotchas

- **Refused types:** `TestExecutionRecord`, `TestResult`, native script steps (`TestScriptStep`) and every
  type not listed above cannot be created or updated through these tools (deferred). Reading them works.
  **`delete_qm_resource` does not filter by type**: it deletes any ETM resource whose URI you give it,
  including results and execution records, so check the URI (`get_qm_resource`) before calling it.
- **Writes are only title and description.** The ETM may require other data for some types; if the server
  refuses a creation, its message is shown unchanged.
- **Live acceptance is pending.** The writes are covered by tests against mocks; a real `TestCase` creation
  was verified on 16 Nov 2026, while plans, suites and scripts follow the same pattern and are still to be
  confirmed. Prefer a disposable item when trying them on a real server.
- **After a write whose confirmation read failed**, the error says the PUT SUCCEEDED and the
  resource WAS changed: re-read before retrying; never resend blindly.
- A project's ETM query capabilities and the Reportable REST alias can differ from its title; the tools
  resolve them for you.
- Setup, TLS and error gates: see the **`elm-discovery`** skill ("Setup and errors").
