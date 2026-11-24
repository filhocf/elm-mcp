---
name: elm-traceability
description: Build and inspect traceability links in IBM ELM through elm-mcp: link work items to each other, link a work item to requirements, test cases and other artifacts, and link test cases to requirements and work items; check test coverage. Use when you need requirement-to-work-item-to-test traceability, or must add or remove a link between ELM artifacts.
metadata:
  version: "0.2.0"
  keywords: "traceability, link, unlink, implements requirement, tracks requirement, validates requirement, tested by test case, tests change request, coverage, rm, ccm, qm, elm"
---

# ELM Traceability — links across RM, CCM and QM

## Purpose

Connect artifacts across the three ELM domains and check the result. Three pairs of tools write links, one
for each starting point; all of them re-read the resource after the PUT and confirm the final set of links.

| You start from | Tool | Links to |
|---|---|---|
| a work item (CCM) | `link_workitems` / `unlink_workitems` | another work item |
| a work item (CCM) | `link_artifacts` / `unlink_artifacts` | an RM requirement or a QM artifact |
| a test case (QM) | `link_test_case` / `unlink_test_case` | an RM requirement or a CCM work item |

To **read** the picture: `get_test_coverage(project)` (per test case: requirements validated, work items
tested) and `get_qm_resource(uri)` (its `links`). See the **`elm-qm`** skill.

## Choose the right tool

- Requirement implemented by a work item: `link_artifacts(source_uri=<work item URI>,
  target_uri=<requirement URI>, link_type="implementsRequirement")`.
- Work item tested by a test case: `link_artifacts(..., link_type="testedByTestCase")`, or from the
  test side `link_test_case(test_case=<TestCase URI>, target=<work item URI>, link_type="testsChangeRequest")`.
- Test case validates a requirement: `link_test_case(test_case=<TestCase URI>, target=<requirement URI>,
  link_type="validatesRequirement")`.
- Parent/child, blocks, depends on, related, duplicate, resolves between work items:
  `link_workitems(source, target, link_type)` with `link_type` in `parent`, `blocks`, `depends_on`,
  `related`, `duplicate_of`, `resolves`. `parent` **replaces** the current parent.

`link_artifacts` link types (closed list): `implementsRequirement`, `tracksRequirement`,
`affectsRequirement`, `testedByTestCase`, `relatedTestCase`, `relatedTestScript`, `relatedTestPlan`,
`relatedTestExecutionRecord`, `affectsPlanItem`, `affectedByDefect`, `affectsTestResult`,
`blocksTestExecutionRecord`, `relatedChangeRequest`.

## Rules every link tool follows

- **Inputs.** `link_workitems`/`unlink_workitems` take work item **ids**. `link_artifacts` and
  `link_test_case` take full resource **URIs** on the configured `ELM_URL`; another host is refused before
  any request. `link_test_case` also requires `test_case` to be a QM resource path, and `target` to be a plain URI
  (no query string, fragment, `%` or `..`) of an RM requirement (`validatesRequirement`) or of a CCM work
  item (`testsChangeRequest`); `unlink_test_case` only checks the origin of the target.
- **The source must be the right kind.** `link_artifacts` needs a CCM work item URI as `source_uri`;
  `link_test_case` needs a TestCase URI. Source and target must differ.
- **Idempotent.** Linking something already linked returns `changed: false` and writes nothing.
  Unlinking a target that is not linked is an error and writes nothing.
- **Other links are preserved.** The PUT resends the links that were already there; only the one you
  asked for is added or removed. Concurrent edits give a `[conflito]` (412): re-read and retry.
- **Confirmation.** Every tool re-reads after the PUT. If the re-read fails or disagrees:
  `link_test_case` / `unlink_test_case` say the PUT SUCCEEDED and the test case WAS changed;
  `link_workitems` and `link_artifacts` say the server accepted the write but the re-read does not show
  the expected links (`link_artifacts` lists the lost and unexpected targets), and do not retry.
  In every case read the resource before trying again.
- Removal is symmetric: use the `unlink_*` tool with the same arguments.

## Gotchas

- **The write format of `link_artifacts` is still to be confirmed in live acceptance**, and `link_test_case`
  is verified against mocks only. Try both first on a disposable item.
- `get_requirement(uri)` returns the RM requirement as it is stored; the links appear on the work item or
  test case that owns them, not always on the requirement.
- Deleting a requirement or test case (`delete_requirement`, `delete_qm_resource`) is irreversible and
  does not clean the other side's links: unlink first when traceability must stay consistent.
- For discovery, setup and error gates see the **`elm-discovery`** skill.
