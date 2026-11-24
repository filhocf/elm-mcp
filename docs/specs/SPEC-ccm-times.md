# SPEC — CCM: listar times (Team Areas) de uma project area

**Status:** PLANNED (G0 pendente: descobrir o endpoint por leitura viva) · **Release:** v0.7.0 · **Origem da ideia:** tool `listar_times` do cadu-ia (só a necessidade; a implementação é nossa, ADR-006)

## Contexto
Criar work item exige `team_area`/`filed_against` válidos por project area. Hoje o usuário descobre o time por `inspect_workitem` de um item existente ou por `list_fields`. Falta uma forma direta de listar os times ativos.

## Critério de aceite (EARS)
- QUANDO `list_teams(project)` for chamado, o sistema DEVE devolver os times ativos da project area (`name`, `uri`, `id`), ordenados por nome, com `total`.
- O sistema DEVE aceitar o nome da project area (como `list_types`) e usar o cache de discovery (TTL, `refresh=True`).
- SE a project area não tiver o recurso de times acessível pelo usuário, ENTÃO o sistema DEVE devolver erro com o gate provável (SPEC-diagnostico-gate), sem listar vazio como se fosse sucesso.
- A tool DEVE ser somente leitura (`readOnlyHint`), e a contagem de tools em `AGENTS.md`/`docs/TOOLS.md` DEVE ser atualizada no mesmo PR.

## G0 (obrigatório antes do RED)
Descobrir, em leitura viva, o endpoint suportado: candidatos (1) `rtc_cm:teamArea` em `allowedValues` do shape do work item (já lido por `list_fields`), (2) catálogo/ServiceProvider de Process (`/ccm/process/project-areas/<id>/team-areas`), (3) consulta OSLC de `process:TeamArea`. Escolher a rota de API suportada; NÃO usar serviços internos (`internal.rest`). Registrar a evidência (fixture sanitizada em `tests/fixtures/`).

## Gates
G0 → G3 (respx com a fixture) → G4 → G5 → aceitação viva (somente leitura).
