# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added

- `list_fields` titles from the enumeration (#77) are hardened: path segments must match `[A-Za-z0-9_.-]+` (no `.`, `..`, `%`), URIs with userinfo are never fetched, the enumeration read is capped at 5 MB; `set_field` refuses an id that equals the label of another value as ambiguous (the full URI disambiguates).
- Skills brought up to v0.6: `elm-discovery`, `elm-requirements` and `elm-workitems` updated (required `type` on `create_requirement`, `if_exists`, no automatic category, profile, cache and `refresh`, hours and attachments, TLS/password setup and error gates; removed claims that were no longer true), and two new skills, `elm-qm` (ETM reads, title/description writes, links) and `elm-traceability` (which link tool to use from a work item, a test case or a requirement). `docs/TOOLS.md` and `docs/USAGE.md` no longer say that QM is read-only or that the category is auto-discovered.
- `list_teams(project, include_archived=False, refresh=False)` (read only): lists the team areas of a CCM project area with one Process REST call, returning `{"items": [{name, id, uri, process_uri, archived}], "total"}` sorted by name; `uri` is the `rtc_cm:teamArea` value meant for `create_workitem(custom_fields=...)` and `update_workitem(custom_fields=...)` (writing it through `create_workitem(custom_fields=...)` was validated live on 24/nov/2026). Team ids outside `[A-Za-z0-9_.-]+` (and `.`/`..`) are dropped, DTD/entity checks are case-insensitive, and `rdf:resource` values in the create payload are now attribute-escaped. Archived teams are left out by default; the answer is cached like the other discovery calls. The tool count is now 41 (22 read-only, 19 not read-only) and the contract snapshot was regenerated.
- `create_requirement` and `update_requirement` take `content_format` (`html` default, `xhtml`, `markdown`; another value is refused before any request). Markdown is converted by a built-in converter (`markdown_xhtml.py`, no new dependency); the stored text has exactly one root `div` even when the content already carries it; `script`, `style`, `iframe`, `object`, `embed` and characters above U+FFFF are removed from HTML.
- `create_requirement`: after an explicit 403 on the POST with content larger than `ELM_RM_CREATE_FALLBACK_BYTES` (default 4096), the requirement is created without text and the content is applied by PUT with `If-Match`, the PUT must be accepted (2xx) and the re-read confirms that the requirement exists and its ETag changed (the content itself is not compared); the result carries `content_applied` (`false` plus a `warning` when the PUT fails, with nothing deleted).
- Hardening of the requirement content path (review loop 1): the HTML sanitizer, the Markdown converter and the root-div unwrapping run in linear time (no regex backtracking on `<script `, `<img `, `[`, `![`, `[x](`, heading spaces; 200 KB in well under 1 s); content above `ELM_RM_MAX_CONTENT_BYTES` (default 1048576 bytes) is refused naming the variable; link labels are limited to 1000 and URLs to 2000 characters, and one level of balanced parentheses is accepted in a URL (`[a](http://x/a(b))`).
- `prepare_content` (`html` and `xhtml`) refuses comments (`<!--`), CDATA (`<![CDATA[`) and processing instructions (`<?`) with a clear error; `update_requirement` replaces the primaryText up to its LAST closing tag. After the PUT, `update_requirement` warns when the re-read returns the same ETag as before (the change is not confirmed). `ELM_RM_CREATE_FALLBACK_BYTES` zero, negative or invalid uses the default 4096. Lone surrogates are removed; the root `div` is also recognised with a single-quoted `xmlns`.
- Review loop 2: the whole HTML cleaning now repeats until the result is stable (tags spliced together by the removal of `<head></head>`, `<html>`, emojis or lone surrogates, e.g. `<scr<head></head>ipt>`, no longer turn into `<script>`); nested root `div`s unwrap in linear time (20000 levels in under a second); an empty self-closing `jazz_rm:primaryText` can be updated.
- Known limits: `content_format="xhtml"` only validates and wraps, it does NOT remove active content (`script`, `style`...); in `html`, an unclosed `<script>x` (or one without `>`) removes everything up to the end of the text.

ETM (QM) write, scope by evidence (SPEC-qm-write, wave W6). Creation (after the typed-body fix #78), editing, links, deletion and coverage were validated live on 24/nov/2026:

- `create_qm_resource(project, type, title, description=None, if_exists="error")`: creates a `TestCase`, `TestPlan`, `TestSuite` or `TestScript` by POSTing RDF/XML (one typed node, `rdf:about=""`, with only `dcterms:title` + `dcterms:description`) to the creation factory found by `oslc:resourceType` (local name, `oslc_qm` and `rqm` namespaces). The title is escaped and stripped of control characters. `uri` and `short_id` are read back from the `Location`. Duplicate check by normalized title among resources of the same type (`if_exists` as in `create_requirement`); the POST is never resent after a timeout. `TestExecutionRecord`, `TestResult`, native script steps and other types are refused as deferred.
- `update_qm_resource(uri, title=None, description=None)`: re-reads the resource and sends the complete RDF back with `If-Match`, changing only the title/description (byte edit of what the server sent: links, rich-text sections and namespaces are preserved). A 412 is a `[conflito]` error, not retried; a resource without ETag is never written.
- `delete_qm_resource(uri)`: DELETE confirmed by re-reading (404/410); a denied confirmation read reports that the deletion was not confirmed.
- `link_test_case(test_case, target, link_type)` / `unlink_test_case(...)`: adds or removes `oslc_qm:validatesRequirement` (RM requirement) or `oslc_qm:testsChangeRequest` (CCM work item) on a TestCase, preserving the other links, and confirms that the final set equals the expected one by re-reading.
- G5 final fix (W6): a failure or divergence in the re-read after a successful PUT (`update_qm_resource`, `link_test_case`, `unlink_test_case`) now says the PUT SUCCEEDED and the resource WAS changed, and not to retry blindly; the post-PUT comparison ignores whitespace/CRLF differences and tolerates properties added by the server.
- G5 loop 2 (W6): QM URIs are matched anchored (no `..`, `%`, backslash, query; `/qm/oslc_qm/contexts/<p>/resources/<type>/<id>` only), RM/CCM link targets likewise; the edited body is re-parsed and checked BEFORE the PUT (update and link/unlink), `update_qm_resource` re-reads and confirms after the PUT; a 201 with a foreign `Location` says the resource may exist; title <= 255 and description <= 32768 characters; weak ETags, titles kept as XML attributes and unlinks that would leave a reified `rdf:Statement` are refused.

Hours and attachments, read only (SPEC-horas, SPEC-ccm-interno, wave W5):

- `list_time(id)`: entries of a work item's time sheet (`entry_uri`, `person`, `date`, `hours`, `time_code`) and `total_hours`. Milliseconds become hours with 2 decimals; null time is 0.
- `get_hours(item_ids=None, plan=None, project=None, group_by="person", max_entries=500)`: hours per person or per work item for the given items and their direct children (the `get_plan_items` selection). Entries are read with at most 8 requests at a time; above `max_entries` it returns a partial result (`partial`, `unread_entries`); an unreadable entry goes to `errors`; an item with `timeSpent` but no entries is marked `without_entries`.
- `list_attachments(id)`: attachment metadata (`uri`, `name` without the identifier prefix, `identifier`, `format`, `creator`, `created`).
- `download_attachment(id, attachment, dest, overwrite=False)`: saves an attachment to a local file (`Accept: */*`, base name of the server's `filename*` only, no overwrite without `overwrite=True`, size cap `ELM_MAX_DOWNLOAD_MB`, default 50, no partial file left). It only sends GET requests to the ALM.
- G5 loop 2: `download_attachment` is no longer flagged read only (`readOnlyHint=false`, `destructiveHint=true`: it writes a local file); the tools are now 21 read-only and 14 write. New optional `ELM_DOWNLOAD_DIR` (destination confined to that root, symlinks and `..` resolved) and `ELM_DOWNLOAD_TIMEOUT` (default 300 s, temporary file removed). Local write errors become clear errors; filesystems without hardlinks are supported without overwriting; names are cut to 255 bytes and control characters dropped; the final file mode is 0666 minus the umask.
- G5 loop 2: re-login after an expired session is serialized (one login per expiry, not one per request). `get_hours`: `max_entries` up to 5000, `partial` is also true when an entry failed, an entry listed by two items counts once.
- Writing time entries and attachments through the internal API stays deferred (no `ELM_ENABLE_INTERNAL_API`).

Discovery cache and RM query (SPEC-cache-shape, SPEC-rm-consulta, wave W4):

- Discovery cache in memory (provider, factories, shapes, allowedValues, workflow, RM stream), per server and project area; TTL from `ELM_CACHE_TTL` (default 300 s, `0` turns it off).
- `refresh=True` on `list_types`, `list_fields` and `list_workflow` ignores the cache and repopulates it.
- `search_requirements(project, query=None, limit=None)` accepts `limit` and returns `total` and `configuration` (the stream used) when known.

Idempotency and local profile (SPEC-idempotencia, SPEC-perfil-template, ADR-005, wave W4):

- `if_exists` (`error` | `return` | `create`) on `create_requirement`, `create_workitem` and `add_child_workitem`: a duplicate (same title, type and folder or parent) is refused, returned or ignored.
- Duplicate check: the server compares the title exactly (case and inner spaces count); the normalization only applies to titles that come back. The query asks for 1000 items and a truncated check refuses the creation (`duplicate check truncated`, except `if_exists="create"`). Without `folder`, `create_requirement` matches a requirement in any folder (accepted false positive).
- After a 400/409 on `create_workitem` the duplicate check runs again before the single resend; an item the server created meanwhile is returned (`created: true`, `warning`) and not duplicated.
- `custom_fields` keys given as a full property URI are normalized to `prefix:name` before the required-field check and the write.
- Hardening: profile files over 1 MB are refused; `list_workitems` degrades with a `warning` when the profile is malformed; a profile alias never shadows a real action; `ELM_CACHE_TTL` negative/`nan`/infinite means 300; the profile draft keeps literal defaults as literals.
- Creation results carry `created: true|false`; a timeout after the POST queries the duplicate again and never resends the POST.
- Local profile in JSON outside the package (`ELM_MCP_PROFILE_DIR`, default `~/.elm-mcp/profiles/`): templates plus project area overrides, with `defaults`, `action_aliases` and `hidden_states`.
- `create_workitem` fills missing required fields from the profile and refuses, listing them, those the shape still requires.
- `transition_workitem` accepts the action aliases of the profile.
- `list_workitems(include_hidden=False)` leaves out the states the profile hides.
- `elm-mcp profile draft <project>` prints a profile draft (types, required fields, allowed values) built from read-only discovery.

ETM reading and cross-domain links (SPEC-qm-leitura, SPEC-oslc-links, wave W3):

- `list_qm_resources(project, type, query=None, limit=None)`: lists any of the 14 ETM types, one item per resource, with `total` from `oslc:totalCount`.
- `get_qm_resource(uri)`: normalized properties and grouped links (`validates_requirement`, `tests_change_request`, scripts, results).
- `get_test_coverage(project)`: requirements validated and work items tested, per test case.
- `get_test_results(project, test_case=None)`: verdict (URI of the verdict resource) and `verdict_name` (its readable final fragment, e.g. `inconclusive`), points per status and originating execution record. Property names confirmed in live reading.
- `link_artifacts(source_uri, target_uri, link_type)`: links a work item to an RM/QM artifact (13 `oslc_cm` types); partial PUT with `If-Match`, confirmed by re-reading. Validated live on 24/nov/2026 for `implementsRequirement`, `affectsRequirement` and `testedByTestCase` (idempotent repeat checked only for `implementsRequirement`); the other types are not yet exercised live.
- `unlink_artifacts(source_uri, target_uri, link_type)`: removes only that link, keeping the others.

CCM collaboration (SPEC-ccm-colaboracao, wave W2):

- `add_comment(id, text)`: POSTs the comment as OSLC CM 1.0 (`application/x-oslc-cm-change-request+json`,
  no `OSLC-Core-Version`) and confirms it appears in the discussion; if it does not, the call fails without
  resending the POST.
- `list_comments(id)`: comments of a work item (`oslc:discussedBy` -> `oslc:comment`) as
  `[{"id", "author", "created", "text"}]`; an item without discussion returns `[]`.
- `link_workitems(source, target, link_type)` / `unlink_workitems(...)`: partial PUT of the link property
  (`rtc_cm:com.ibm.team.workitem.linktype.*`) with `If-Match`, keeping the other links of that type, confirmed by
  re-reading the source. Types: `parent`, `blocks`, `depends_on`, `related`, `duplicate_of`, `resolves`
  (`parent` replaces the current parent).
- `set_field(id, field, value)`: partial PUT of one field; an enumerated field takes the literal title
  (e.g. `5 pts`) or id, validated against `allowedValues`, and warns when `Unassigned` is written to a required
  attribute.
- `list_workflow(project, type)`: states (`id`, `title`, `group`) and action ids of a type's workflow.

- Tool contract snapshot (`tests/contract/tools.snapshot.json`) with a network-free test; regenerate with `uv run python scripts/update_contract.py`. Tools now publish the MCP `readOnlyHint` annotation (read tools `true`, write tools `false`), so clients can tell them apart.
- `delete_requirement` tool: delete an RM requirement (direct mode) and confirm by re-reading it (404 or 410); fails if it is still readable.

### Changed

- `add_child_workitem` takes `filed_against` and `custom_fields` (after `if_exists`) and passes them to `create_workitem`, so a child can be created in one call in a project whose type requires a category or custom attribute (#80). The contract snapshot was regenerated.
- `set_field` on an enumerated field accepts the label (exact, then case- and edge-space-insensitive); an unknown label is refused listing the valid labels, and a label shared by several values is refused as ambiguous, listing their ids (the id or the full URI always works) (#77).
- **BREAKING (#70):** password decoding no longer guesses.
  - `ELM_PASSWORD` is now used as raw text. If yours was base64 without a prefix, write it as `ELM_PASSWORD=b64:<base64>` (the part after `b64:` is stripped of surrounding whitespace). To use a password that literally starts with `b64:`, write `b64:b64:...` (`b64:b64:x` is the text `b64:x`). `b64:` followed by invalid base64 fails at authentication with a clear error naming `ELM_PASSWORD`.
  - A `password` in `~/.elm_creds.json` that is plain text (not base64 of UTF-8) is now an error, with no fallback: the file is only written by `elm-mcp login`, always in base64. Run `elm-mcp login` again. The error is raised when the server tries to authenticate; `import elm_mcp` and `elm-mcp login` still work with an invalid file.
- **BREAKING (#71):** TLS certificate verification is now ON by default (it was always off). For a server with an internal CA, set `ELM_CA_BUNDLE=/path/to/ca.pem` (or `ELM_CA_BUNDLE=system` for the OS trust store); a missing file is a `ValueError` naming `ELM_CA_BUNDLE`. To keep the old behavior, set `ELM_VERIFY_TLS=false` (also `0`, `no`, `off`; it wins over `ELM_CA_BUNDLE` and logs one warning per process). `elm-mcp login` follows the same settings.
- `search_requirements` now returns interpreted items (`uri`, `title`, `identifier`, `type`, `modified`, `folder`) instead of the raw RDF/JSON dictionary: a deliberate change of return.
- RM queries discover the project's stream and send `Configuration-Context`, `oslc.prefix` (dcterms, oslc, rdf, nav, jazz_rm) and `oslc.select`; a project without a stream is queried without the header.
- `if_exists` defaults to `error`: creating a requirement or work item whose title, type and folder or parent already exist is now refused. Pass `if_exists="create"` for the old behavior (deliberate incompatible change).
- `create_workitem` reads the type's shape before the POST and refuses, listing them, required fields without default that neither the call nor the profile fills.
- The CCM query base is read through the discovery cache, so a query no longer refetches the service provider.
- `create_workitem` rediscovers the project area once and resends once when the server answers 400 or 409 (stale shape or value); 403, 404 and 500 are not retried.

- `search_testcases` now returns the normalized ETM format: one item per test case `{uri, title, identifier, short_id, state, type}` plus `truncated` (D12: the TestCase capability is chosen by `oslc:resourceType`, RDF/XML only).
- `list_workitems` gains the optional filters `type`, `state`, `owner` and `planned_for`, sent to the server in
  `oslc.where` (combined with `query` by `and`); a filter value with a quote, angle bracket or backslash is refused.
- `list_workitems` now returns `{"items": [...], "truncated": bool}` instead of a list, follows `oslc:nextPage` up to `limit` items (default 50, max 1000) and re-authenticates once per page.
- **Breaking (W2, deliberate):** `search_requirements` and `search_testcases` now return `{"items": [...], "truncated": bool}` instead of a list (same shape as `list_workitems`), follow the pages up to 1000 items and no longer cut silently at 50. `get_plan_items` also queries up to 1000 items and gains a `truncated` key.
- Pagination (`oslc:nextPage`) stops on a repeated page URL, on a page without any new item and after 100 pages (`truncated=true`); `limit` of zero or less is an error instead of silently using the default.
- Security: every URL followed from a server response (next pages, workflow states, comment and iteration references, shapes) must be on the same scheme, host and port as `ELM_URL`; otherwise the call fails. The same applies to the URI given to `get_requirement`.
- `add_comment` sends the text HTML-escaped (line breaks as `<br/>`) and confirms the comment by its new id (without the created id, only a new comment with the same normalized text confirms it; the sent and the stored text go through the same escape + normalization, so `<b>` and `&` match); the POST is still sent once.
- Work item type resolution has an explicit precedence (exact id, then the last segment of the id, then title); two matches at the same level are an error listing the ids.
- `set_field` refuses a malformed field name (`prefix:name` only), a read-only field and a field outside the item's shape (use `list_fields`), and converts the value to the field's range (integer family, boolean, decimal). Integers are plain ASCII (`1_000` and non-ASCII digits are refused) and respect the sign of their range (`nonNegativeInteger`, `positiveInteger`, `negativeInteger`, `unsigned*`...).
- `create_workitem` discovers the project and its factories once instead of twice.
- `link_workitems` / `unlink_workitems` refuse a link of an item to itself; list_workitems filters also refuse non-printable characters.
- `create_workitem` accepts any work item type the project exposes (id, title or short alias such as `task`/`story`); an unknown type is refused listing the available ones.

- **Breaking:** `create_requirement` now requires `type` (a shape URI or a type title from `list_types("rm")`). Without it, or with an unknown title, the call is refused with the list of valid types and nothing is sent; before, the server answered 403 CRRRS6404E.
- Every HTTP error of a read or write tool is now a `ToolError` that names the probable gate (`credencial`, `sessao`, `regra-de-processo`, `permissao`, `recurso-inexistente-ou-invisivel`, `conflito`, `formato`) and the action to take, instead of a bare `HTTPStatusError`; the server's `oslc:message` is passed through unchanged.
- The main reads now re-authenticate once on a 401 or a 403 without `oslc:message` (writes already did); a failed login no longer leaves a half-authenticated client cached. A login answering 403 or 5xx now fails (`[credencial]` / `[http-<status>]`) instead of being accepted; three direct reads still do not re-authenticate (see `docs/roadmap.md`, "Dívida conhecida").
- `list_folders` now raises "project area not advertised by the service provider" instead of returning `[]` when `oslc:details` is missing.
- `create_requirement`: if the re-read of D8 fails after the POST, it no longer raises (the requirement exists): it returns the URI, the POST-body identifier (or `null`) and a `warning`, and logs a warning.
- `delete_requirement`: a denied (401/403) confirmation read reports "deletion not confirmed" (the DELETE was sent); the DELETE is unconditional (no `If-Match`).
- The contract snapshot grew: each parameter now records its `default` when it declares one; `scripts/update_contract.py` accepts an output path.
- `transition_workitem`: `action` is now the id or id suffix of a workflow action; `start`/`resolve`/`close`/
  `reopen` work only where the workflow's action ids contain those words.

### Fixed

- The hint on a refused `create_workitem` (403) no longer always points to `filedAgainst` (#79). When the server says `The 'X' attribute needs to be set`, the hint names the shape property (or properties) whose title is X (exact match, then case-insensitive; the quoted text is never used as a pattern) with its number of allowed values; without a match the generic hint is unchanged. The server message is still relayed verbatim.
- `list_fields` returned `title: null` for every value of an enumerated field whose `allowedValues` lists only references (#77). A value of this server under `/oslc/enumerations/` now gets its label (`rdfs:label` or `dcterms:title`) from the enumeration document, read once per enumeration through the discovery cache (`refresh` honored); if that document cannot be read the title stays `null` and nothing fails.
- `create_qm_resource` POSTed an untyped `<rdf:Description>`, which a real ETM (7.0.1 SR1) answers with a generic HTTP 400 (#78). The body is now ONE typed anonymous node with `rdf:about=""`: `oslc_qm:TestCase`, `oslc_qm:TestPlan`, `oslc_qm:TestScript` and `rqm_qm:TestSuite` (namespace `http://jazz.net/ns/qm/rqm#`; `oslc_qm:TestSuite` is refused with 400 by the ETM). Creation had only been "verified" by a probe that used a typed node, never through the tool, and the W6 fake accepted untyped bodies; the fake now refuses them with 400 like the real ETM, so the whole suite guards this defect.
- Password decoding no longer corrupts passwords (#70): the `password` in `~/.elm_creds.json` is always decoded as base64 and UTF-8, and `ELM_PASSWORD` is used as is, so a plaintext password that looks like base64, or one with non-ASCII or control characters, comes out intact.
- `search_requirements` no longer answers 400 (`Undefined namespace prefix` / null configuration) on RM queries with `oslc.where`.
- `search_requirements` returns one item per requirement instead of one giant raw dictionary, and takes the total from "Query Results: N".

- D12: `search_testcases` returned one `raw_xml` blob of the first query capability (TestExecutionRecord); it now picks the TestCase capability by `oslc:resourceType` and returns one item per test case.

Live acceptance of W2 (read-only, ELM 7.0.1):

- `list_workitems` filters now use what the server accepts in `oslc.where`: `type` is resolved (id, alias or
  title) by the project's discovery and sent as `rtc_cm:type="<id>"` (`dcterms:type="<title>"` matched nothing);
  `state` is sent as `rtc_cm:state="<state id>"` (a title is resolved by the workflow of the given `type`,
  otherwise the call fails asking for the id; `oslc_cm:status` was an HTTP 400); `owner` is always a user URI.
- `list_workflow` no longer fails with "does not expose its workflow": it reads the allowedValues document of the
  shape's `state` property (`.../property/internalState/allowedValues`) and derives the workflow from the state
  URIs; if the titles document cannot be read, it still returns the state ids.

W1 live-acceptance fixes (ELM 7.0.1):

- `list_folders` returned the query/description resources and dropped the root folder. It now keeps only `nav#folder`
  resources (root included) and returns `{"title", "uri", "subfolders_query"}`.
- `get_requirement` failed with HTTP 415 on RM resources. It now retries with `application/rdf+xml` and returns a flat dict.
- `update_requirement` returned `identifier: null`. It now re-reads the identifier (a failed re-read only adds a `warning`).

CCM corrections verified against ELM 7.0.1 (SPEC-ccm-correcoes, defects D1-D11 of slice F1):

- **D1** `list_types("ccm")` returned property titles ("Children", "Verified"). It now lists one item per typed
  creation factory: `title`, `id`, `factory_title`, `factory_uri`, `shape_uri`, `resource_type`
  (generic factories are not listed).
- **D2** `list_fields("ccm", ...)` failed with `Type 'story' not found`. The type is now resolved by id, title or
  alias (`task`/`story`); fields gain `occurs` and `read_only` (additive, also for rm/qm). Loop 1: the shape parser
  now reads the format the CCM server really uses (`rdf:Description` typed `core#Property`, children in any order,
  `dcterms:title` with `rdf:parseType="Literal"`; the ResourceShape's own Description is ignored) besides the
  `<oslc:Property>` blocks of RM/QM. A shape that references properties but yields none raises an error instead of `[]`.
  `allowedValues` resources that list `oslc:allowedValue` references no longer return their own URI as a value.
- **D6** `create_workitem` / `add_child_workitem` sent `rtc_cm:ownedBy` (HTTP 409). `owner` is now written to
  `dcterms:contributor`, as `update_workitem` already did.
- **D9** `create_workitem` returned HTTP 500 for stories (generic factory). It now POSTs to the factory of the
  type, and a refused write raises a `ToolError` carrying the server's `oslc:message`. Re-authentication is
  retried only on 401 or a login redirect (a 403 with a message is a business error, not resent).
- **D10** `transition_workitem` looked up actions at a URL that does not exist and fell back to a PUT on
  `rtc_cm:state` that the server ignores. It now resolves `action` (id, id suffix or unique substring) against
  the item's workflow, executes `PUT ?_action=<id>` with `If-Match`, re-reads the item and fails when the state
  did not change. Returns `Work item {id}: {from} -> {to} (action: {action id})`.
- **D11** `update_resource` / `update_workitem` PUT the whole merged resource (130+ fields). It now PUTs only the
  changed properties (`?oslc.properties=...`) with `If-Match`, returns the re-read item, and reports a stale
  ETag (412) as a conflict.

- **D3** `list_folders` failed with HTTP 400 because it queried folders by the component URI. It now queries by the project area URI announced by the service provider (`oslc:details`).
- **D8** `create_requirement` returned `identifier: null` when the POST body was empty. It now re-reads the created artifact and returns the server's `dcterms:identifier`.
- **D13** `create_workitem` without `filed_against` no longer sends a category (the server applies its own default) and
  no longer looks one up; a refusal about the category now adds a hint to use `list_fields` (property `filedAgainst`).

### Security

- URLs followed from server responses, and the URI given to `get_requirement`/`get_resource`, are accepted only
  on the same origin as `ELM_URL` (scheme, host and port; default ports 80/443 are normalized), otherwise the call
  fails before any request (`assert_same_origin`). Set `ELM_URL` to the exact public URL the server announces.
- HTTP redirects are checked too: a 3xx whose `Location` leaves that origin is refused before it is followed
  (response hook on the client). The login flow shares the rule, so an http->https or other-host redirect at login fails.

## [0.5.0] - 2026-11-23

RM write and discovery: the agentic membrane for DOORS Next.

### Added

- Discovery tools `list_types`, `list_fields`, `inspect_workitem` and `list_folders` — enumerate types, shape fields and folders per project area, so callers adapt to each project instead of hardcoding (#45).
- `create_requirement` tool — create a requirement in DOORS Next (RM) via OSLC; HTML content is
  sanitized to XHTML and stored in `jazz_rm:primaryText`; optional `folder` (nav:parent) and `type`
  (instanceShape URI).
- `update_requirement` tool — update an RM requirement (title/content) via OSLC PUT with ETag; a 412
  conflict surfaces as a readable `ToolError`.

## [0.4.0] - 2026-10-28

Renamed from `elm-alm-py` to **`elm-mcp`** and professionalized into a first-class OSS project.

### Changed

- **Renamed package `elm-alm-py` → `elm-mcp`** (distribution, module `elm_mcp`, CLI command `elm-mcp`, repository).
- Migrated to MCP SDK 2.x: `FastMCP` → `MCPServer`; `ToolError` for user-facing errors; pinned `mcp>=2.3,<3`.
- `add_child_workitem` requires an explicit `project` (removed the hardcoded default).

### Removed

- Dead code: `_find_creation_factory` (no production caller) and its two tests.
- All organization-specific references from code and tests (vendor-neutral: generic placeholders only).

### Added

- `get_plan_items` tool — hierarchical sprint view returning backlog items with their child tasks expanded in a single call, selected by `item_ids` or by `plan` (iteration title substring) (#26)
- `transition_workitem` tool — move a work item through its workflow (`start`, `resolve`, `close`, `reopen`), discovering the target state from the work item's workflow actions (#25)

### Changed

- `list_workitems` requests key fields via `oslc.select` and returns `id`, `title`, `type`, `status` and `owner` inline instead of bare resource URIs (#23)

### Fixed

- Pin `mcp<2`: mcp 2.x removed `mcp.server.fastmcp`, so `elm-alm-py serve` crashed on startup with `ModuleNotFoundError` (#33)
- `create_workitem` no longer fails with HTTP 500 after an idle session: the session is refreshed before the POST, with re-authentication on 401/403 (#24)

## [0.3.0] - 2026-05-22

### Added

- `update_workitem` accepts `owner`, `estimate_hours`, `planned_for` and `custom_fields` (#20)
- `list_iterations` tool — list a project's sprints/releases with title, start/end dates and URI (#11)
- `docs/USAGE.md` with working OSLC query patterns, examples and known limitations (#13)

### Changed

- README rewritten to cover write operations, `custom_fields` and OSLC details

### Fixed

- Current-iteration discovery picks the iteration whose start/end dates contain today, preferring bounded iterations over open-ended ones (#19)

## [0.2.3] - 2026-05-22

Tagged but not published to PyPI (package version was not bumped); these changes first shipped in 0.3.0.

### Fixed

- `create_workitem` POSTs directly to the project's `/ccm/oslc/contexts/{projectAreaId}/workitems` endpoint instead of relying on creation-factory discovery (#15, #17)
- Extract `projectAreaId` correctly from project URLs ending in `/workitems/services.xml`

## [0.2.2] - 2026-05-22

Tagged but not published to PyPI (package version was not bumped); these changes first shipped in 0.3.0.

### Added

- `custom_fields` parameter on `create_workitem` for project-specific RDF fields
- `create_workitem` sets `plannedFor` to the project's current iteration automatically

## [0.2.1] - 2026-05-22

### Fixed

- Correct creation factory selection by work item type — no longer picks wrong factory (e.g., defect instead of task) (#7)
- Auto-discover default category (filedAgainst) for create_workitem when not provided (#8)
- Use RDF/XML format (application/rdf+xml) for resource creation POST instead of JSON (#9)

### Changed

- Remove all hardcoded organization-specific references from README, code, tests, and docs
- ELM URL is now fully configurable via ELM_URL env var or ~/.elm_creds.json (no default)
- Bump actions/checkout from 4 to 6, actions/setup-python from 5 to 6, astral-sh/setup-uv from 4 to 7, github/codeql-action from 3 to 4

## [0.2.0] - 2026-05-21

### Added

- **Write operations** for CCM work items:
  - `create_workitem` — create task/story with parent, owner, description
  - `update_workitem` — update title/description via OSLC PUT with ETag
  - `add_child_workitem` — convenience wrapper for creating children
- OSLC CreationFactory discovery for CCM domain
- ETag-based optimistic locking on updates
- Error handling tests for HTTP 400/401/409/412

### Fixed

- RM (DOORS Next): support Discovery 1.0 namespace in catalog + /views query endpoint
- QM (ETM): fallback to XML when JSON returns 406
- CCM (RTC): derive /services endpoint from catalog URL (was failing on services.xml)
- Handle query_base URLs with existing query parameters (RM projectURL)

### Added

- AGENTS.md with domain-specific OSLC patterns documented
- MEMORY.md for session handoff
- CodeQL security scanning workflow
- Dependabot for pip + github-actions
- Publish workflow (PyPI trusted publisher on tag push)
- pytest-cov with 60% threshold (cli.py excluded)

## [0.1.0] - 2026-05-18

### Added

- Initial implementation: MCP server for IBM ELM via OSLC REST
- 6 tools: list_projects, search_requirements, get_requirement, list_workitems, get_workitem, search_testcases
- Jazz form-based authentication with cookie management
- OSLC service discovery (rootservices → catalog → ServiceProvider → QueryCapability)
- CLI with `login` (credential setup) and `serve` (MCP stdio) commands
- Auto-load credentials from `~/.elm_creds.json` with base64 decode
- Support for ELM 7.0.3 OSLC namespaces (v1 + v2 fallback)
- Comprehensive test suite with respx mocking (21 tests)
- CI: GitHub Actions (ruff lint + pytest on Python 3.10-3.13)
