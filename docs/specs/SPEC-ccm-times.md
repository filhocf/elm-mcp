# SPEC — CCM: listar times (Team Areas) de uma project area

**Status:** IMPLEMENTADA (G4, aguarda G5 e aceitação viva) (G0 FEITO em leitura viva, 24/nov/2026) · **Release:** v0.7.0 · **Origem da ideia:** tool `listar_times` do cadu-ia (só a necessidade; a implementação é nossa, ADR-006)

## Contexto
Criar work item exige `team_area`/`filed_against` válidos por project area. Hoje o usuário descobre o time por `inspect_workitem` de um item existente ou por `list_fields`. Falta uma forma direta de listar os times ativos.

## G0 — evidência (leitura viva, 24/nov/2026, ELM 7.0.1 SR1)
1. O shape do work item expõe `rtc_cm:teamArea` (`range` = `rtc_cm:Team`) **sem `allowedValues`** (lista vazia): a rota (1) da versão anterior desta SPEC não serve.
2. `GET /ccm/process/project-areas/{paId}/team-areas` (Process REST, `Accept: text/xml`; não oferece JSON) devolve, numa chamada, os times da project area: `jp06:team-area jp06:name`, `jp06:url` (termina em `/team-areas/{id}`) e `jp06:archived` (`true`/`false`). Verificado: 10 times, todos `archived=false` no projeto de referência.
3. O id do time é o mesmo que o work item usa: o `rtc_cm:teamArea` de um item real é `{origem}/ccm/oslc/teamareas/{id}` e o `process:teamArea` é `{origem}/ccm/process/team-areas/{id}`; o id do item real estava na lista do item 2 e `GET /ccm/oslc/teamareas/{id}` (OSLC, `dcterms:title`) confirmou o mesmo nome.
4. `GET /ccm/oslc/teamareas` (OSLC) lista os times de TODAS as project areas (paginado, só URIs, sem filtro por área): exigiria um GET por time. Rejeitada.
5. O `paId` sai da URL do service provider da project area (`/ccm/oslc/contexts/{paId}/workitems/services.xml`), já usada pelo discovery.
6. Fixture sanitizada (estrutura real, nomes e ids sintéticos): `tests/fixtures/ccm_process_team_areas.xml`.

## Decisão
Rota: **Process REST** (item 2), por project area, uma chamada. É a API de processo documentada da Jazz, não serviço interno. O resultado devolve, por time: `name`, `id`, `uri` (= `rtc_cm:teamArea`, a forma `/ccm/oslc/teamareas/{id}` pronta para `create_workitem(custom_fields=...)`/`set_field`), `process_uri` (`/ccm/process/team-areas/{id}`) e `archived`. Times arquivados ficam de fora por padrão (`include_archived=False`).

## Critério de aceite (EARS) — ajustado pelo G0
- QUANDO `list_teams(project, include_archived=False, refresh=False)` for chamado, o sistema DEVE devolver `{"items": [{name, id, uri, process_uri, archived}], "total"}`, ordenado por `name` (casefold), sem os arquivados salvo `include_archived=True`.
- O sistema DEVE derivar o `paId` do service provider da project area e montar a URL do Process REST sob a MESMA origem de `ELM_URL` (`assert_same_origin`); NÃO DEVE seguir URL de outra origem vinda da resposta.
- O sistema DEVE tratar a resposta XML sem expandir entidades (recusar DTD/`<!ENTITY>` como já faz o parser de RDF/XML) e ignorar blocos sem nome ou sem URL.
- O sistema DEVE usar o cache de discovery (TTL) e `refresh=True` o ignora.
- SE o Process REST responder 401/403/404, ENTÃO o sistema DEVE devolver erro com o gate (SPEC-diagnostico-gate), nunca lista vazia como sucesso.
- A tool DEVE ser somente leitura (`readOnlyHint`), e `AGENTS.md`/`docs/TOOLS.md`/CHANGELOG/snapshot de contrato DEVEM ser atualizados no mesmo PR (41 tools: 22 de leitura + 19 que não são somente leitura).

## Gates
G0 → G3 (respx com a fixture) → G4 → G5 → aceitação viva (somente leitura).
