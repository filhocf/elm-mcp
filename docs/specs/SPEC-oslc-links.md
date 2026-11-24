# SPEC — Links OSLC cross-domínio (rastreabilidade)

**Status:** DONE (G4 GREEN); escrita de link validada ao vivo em 24/nov/2026 para `implementsRequirement`, `affectsRequirement` e `testedByTestCase`; os outros 10 tipos NÃO foram exercitados · G0 feito (shape lido ao vivo 23/nov) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5
**Pesquisa:** `pesquisa-apis-elm.md §1/§3` · `levantamento-interno-alm.md §C.8/C.9/C.10`
**Classe:** entregável de produto — fecha a matriz AC→código→teste→ALM (fase ⑨ do DAIA)

## Meta

Criar e remover links OSLC entre artefatos de domínios diferentes, habilitando a rastreabilidade que o
processo exige: requisito ↔ work item ↔ caso de teste.

## Escopo

- Tool `link_artifacts(source_uri, target_uri, link_type)` — cria link OSLC (propriedade RDF) via PUT
  parcial (`oslc.properties`).
- Tipos suportados (lista fechada, verificada no shape): os de `oslc_cm` acima. Os mais usados: `implementsRequirement`, `tracksRequirement`, `affectsRequirement` (WI→Requisito), `testedByTestCase`, `relatedTestCase` (WI→Caso de Teste), `affectedByDefect` (WI→Defeito).
- Remoção de link (PUT que omite a propriedade).

## Evidência G0 (shape do work item lido ao vivo, 23/nov/2026)
O shape de work item (task e story) expõe, no namespace `http://open-services.net/ns/cm#` (prefixo `oslc_cm`), as propriedades de link
cross-domínio: `implementsRequirement`, `tracksRequirement`, `affectsRequirement`, `testedByTestCase`, `relatedTestCase`,
`relatedTestScript`, `relatedTestPlan`, `relatedTestExecutionRecord`, `affectsPlanItem`, `affectedByDefect`, `affectsTestResult`,
`blocksTestExecutionRecord`, `relatedChangeRequest`, `tracksWorkItem`, `trackedWorkItem`, `tracksChangeSet`. Os links WI↔WI do RTC
(`rtc_cm:...linktype.*`) já são tratados por `link_workitems` (W2).

**Escopo da W3:** `link_artifacts` com a ORIGEM sendo um work item (CCM) e o destino em RM ou QM, escrevendo `oslc_cm:<tipo>` por PUT
parcial. Escrever links a partir do QM (`oslc_qm:validatesRequirement`, `oslc_qm:testsChangeRequest`) depende de escrita no QM e fica
na `SPEC-qm-write` (W6). A escrita foi verificada ao vivo em 24/nov/2026 (itens descartáveis): `link_artifacts`/`unlink_artifacts` com `implementsRequirement` (work item → requisito RM), `affectsRequirement` e `testedByTestCase` (work item → caso de teste ETM) vincularam e desvincularam, e a releitura confirmou; repetir o vínculo devolveu `changed: false` só no caso de `implementsRequirement` (nos outros dois não foi repetido). O formato do PUT é o mesmo de `link_workitems`. Os demais 10 tipos da lista seguem sem verificação ao vivo.

## Critério de aceite (EARS)

- QUANDO `link_artifacts` for chamado com origem, destino e tipo válido, o sistema DEVE criar o link e
  confirmar sua presença no recurso de origem.
- SE o `link_type` for desconhecido, ENTÃO o sistema DEVE recusar com `ToolError` listando os tipos válidos.
- QUANDO um link for removido, o sistema DEVE preservar as demais propriedades do recurso (PUT parcial
  com ETag).
- O sistema DEVE usar namespace `oslc_cm:` (propriedades `oslc_cm:<tipo>`; o prefixo é resolvido pelo mapa `prefixes` do item), sem tipos de organização específica.

## Gates
G0 → G3 (respx: criar link, tipo inválido, remoção preserva resto) → G4 → G5.

## Fontes
- **F2**: `pesquisa-apis-elm.md §1` (links OSLC são propriedades RDF via PUT `oslc.properties` [VIA OSLC]).
- **F3**: `levantamento-interno-alm.md §C.8/C.9/C.10` (implementsRequirement, testedByTestCase, affects).
