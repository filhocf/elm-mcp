# SPEC — Tipos de work item além de task/story

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5
**Pesquisa:** `levantamento-interno-alm.md §C.14` · **Classe:** entregável de produto

## Meta

Permitir criar work items de qualquer tipo que o projeto exponha (defeito, dívida técnica, risco,
impedimento, tipos custom), em vez do `TYPE_IDS` hardcoded atual (só `task`/`story`).

## Escopo

- Resolver o tipo de WI pelo **shape descoberto** (via `SPEC-discovery-tools`), não por um mapa fixo
  no código.
- `create_workitem` aceita qualquer `type` que o discovery da PA reconheça.
- Remover/aposentar o `TYPE_IDS` fixo do core (vira resolução dinâmica + perfil de adaptação).

## Critério de aceite (EARS)

- QUANDO `create_workitem` for chamado com um tipo presente no shape da PA, o sistema DEVE criar o WI
  daquele tipo.
- SE o tipo não existir na PA, ENTÃO o sistema DEVE recusar com `ToolError` listando os tipos disponíveis.
- O sistema NÃO DEVE conter identificadores de tipo específicos de organização no código.

## Gates
G0 → G3 (respx: tipo custom válido, tipo inexistente) → G4 → G5. Depende de `SPEC-discovery-tools`.

## Fontes
- **F3**: `levantamento-interno-alm.md §C.14` (Defeito/Dívida/Risco/Impedimento; tipos custom por PA).
