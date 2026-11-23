# SPEC — Links OSLC cross-domínio (rastreabilidade)

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5
**Pesquisa:** `pesquisa-apis-elm.md §1/§3` · `levantamento-interno-alm.md §C.8/C.9/C.10`
**Classe:** entregável de produto — fecha a matriz AC→código→teste→ALM (fase ⑨ do DAIA)

## Meta

Criar e remover links OSLC entre artefatos de domínios diferentes, habilitando a rastreabilidade que o
processo exige: requisito ↔ work item ↔ caso de teste.

## Escopo

- Tool `link_artifacts(source_uri, target_uri, link_type)` — cria link OSLC (propriedade RDF) via PUT
  parcial (`oslc.properties`).
- Tipos suportados (genéricos IBM/Jazz): `implementsRequirement` (WI→Requisito),
  `testedByTestCase` (WI→Caso de Teste), `affectsPlanItem`/`affects` (Defeito→WI).
- Remoção de link (PUT que omite a propriedade).

## Critério de aceite (EARS)

- QUANDO `link_artifacts` for chamado com origem, destino e tipo válido, o sistema DEVE criar o link e
  confirmar sua presença no recurso de origem.
- SE o `link_type` for desconhecido, ENTÃO o sistema DEVE recusar com `ToolError` listando os tipos válidos.
- QUANDO um link for removido, o sistema DEVE preservar as demais propriedades do recurso (PUT parcial
  com ETag).
- O sistema DEVE usar namespaces IBM/Jazz genéricos (`calm:*`), sem tipos de organização específica.

## Gates
G0 → G3 (respx: criar link, tipo inválido, remoção preserva resto) → G4 → G5.

## Fontes
- **F2**: `pesquisa-apis-elm.md §1` (links OSLC são propriedades RDF via PUT `oslc.properties` [VIA OSLC]).
- **F3**: `levantamento-interno-alm.md §C.8/C.9/C.10` (implementsRequirement, testedByTestCase, affects).
