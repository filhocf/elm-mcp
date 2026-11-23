# Conventions

Decisions that get re-litigated unless written down. Each entry is a **rule** (violating it is a
defect) or a **practice** (current habit, open to change). Update this file when a decision is made.

Related: `AGENTS.md` (architecture index), `docs/adr/` (decisions), `docs/specs/` (requirements),
`docs/roadmap.md` (releases).

## Language

| Artifact | Language | Kind |
|---|---|---|
| `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/CONVENTIONS.md`, `docs/TOOLS.md`, `docs/USAGE.md` | English | rule — public, read by international users |
| Code, docstrings, comments | English | rule |
| `docs/roadmap.md`, `docs/rfc/`, `docs/adr/`, `docs/specs/` | PT-BR | rule — internal, read and reviewed by the maintainer |
| Commit messages | PT-BR subject and body | practice |

Rationale: the project is international (public surface in English), but the design record is
reviewed by the maintainer, who reviews fastest in PT-BR. This mirrors the orc-hestrator convention.

## Tenant neutrality

- **rule** — the package (`src/`) and public docs carry **zero** organization-specific strings
  (no company name, host, project name, or custom namespace). Generic placeholders only:
  `https://elm.company.com`, `com.company.*`, `"MY PROJECT (KEY)"`, user `jdoe`.
- **rule** — concrete per-project values live in configuration / an adaptation profile, discovered
  via OSLC discovery, never hardcoded. See `docs/adr/ADR-003`.
- Tests may use the generic placeholders above as mock data; never real tenant values.

## Requirements notation (EARS, in PT-BR)

Acceptance criteria in `docs/specs/` use EARS, written entirely in PT-BR (no mixed English keywords):

| EARS type | Form |
|---|---|
| Ubiquitous  | `O sistema DEVE …` |
| Event-driven | `QUANDO ‹gatilho›, o sistema DEVE …` |
| State-driven | `ENQUANTO ‹estado›, o sistema DEVE …` |
| Unwanted     | `SE ‹condição›, ENTÃO o sistema DEVE …` |
| Optional     | `ONDE ‹recurso presente›, o sistema DEVE …` |

`DEVE` is the RFC 2119 term in Portuguese (MUST/SHALL). Keep the vocabulary fixed; do not mix English
keywords into PT-BR prose.

## Docs structure

- **rule** — traceability chain **roadmap → RFC → atomic SPEC → PRs**. One SPEC per deliverable;
  no big-bang RFC bundling many increments.
- Each SPEC header carries: `Status (gate) · Release · RFC-mãe §X · Pesquisa §Y · Classe`.
- Cross-cutting decisions go to `docs/adr/` (numbered). Superseded docs move to `docs/_archive/`.

## CHANGELOG

- Keep a Changelog sections only: Added / Changed / Deprecated / Removed / Fixed / Security.
- One line per change, phrased as the effect on the consumer. Rationale goes to the spec/PR.
- Atomic fragments under `changelog.d/` when the repo-gold CI lands (avoids merge conflicts).

## CI / quality gate

- **rule** — the CI runs ruff (latest), the test matrix, and coverage with a gate. Validate locally
  with `uvx ruff@latest`, not the venv's ruff (the CI uses latest and may flag newer rules).
- **practice** — moving toward the repo-gold checklist (versioned `.codebase-memory/`,
  `tests-prove-fix`, SHA-pinned actions, in-repo review skill). See `docs/specs/SPEC-repo-gold-ci.md`.
- **rule** — the tool contract (names, parameters with type and required flag, read-only flag) is frozen in
  `tests/contract/tools.snapshot.json` and checked by `tests/test_w1_contract.py` (no network, no credentials).
  After an INTENTIONAL change to a tool, regenerate it with `uv run python scripts/update_contract.py` and record
  the change in `CHANGELOG.md`. Removing/renaming a tool or parameter, or a new required parameter, is a breaking
  change: minor version bump (0.x) and a line under `### Changed`/`### Removed`.
