# SPEC — QM (ETM): leitura correta e completa

**Status:** G0 feito (leitura viva 23/nov) · **Release:** v0.6.0 · **Guarda-chuva:** `SPEC-ewm-delta` F6 · **Antecede:** `SPEC-qm-write`

## Contexto (verificado no servidor de referência, ETM 7.0.1 SR1)
- O ETM responde só RDF/XML (JSON dá 406).
- Há 14 query capabilities por projeto (TestPlan, TestCase, TestScript, TestScriptStep, TestSuite, TestExecutionRecord,
  TestSuiteExecutionRecord, TestResult, TestSuiteResult, TestData, Keyword, TestEnvironment, BuildRecord, BuildDefinition).
- O catálogo de service providers usa atributos extras no elemento; parser ingênuo não encontra os projetos.
- Links cross-domínio estão nas propriedades `oslc_qm:validatesRequirement` (→ RM) e `oslc_qm:testsChangeRequest` (→ CCM).
- O ETM expõe também a sua Reportable REST (`IIntegrationService`), endereçada por **alias** de projeto, que pode diferir do título.

## Evidência G0 (leitura viva, 23/nov/2026)
1. **A capability escolhida hoje está errada.** `_find_query_base("qm", ...)` devolve a PRIMEIRA `QueryCapability` do `services.xml`, que é a de `TestExecutionRecord`. Logo `search_testcases` consulta execuções, não casos de teste. É parte do D12. A capability certa se escolhe por `oslc:resourceType` (`oslc_qm:TestCaseQuery`, etc.), nunca pela posição.
2. Os 14 `resourceType` são `TestPlanQuery`, `TestCaseQuery`, `TestScriptQuery`, `TestScriptStepQuery`, `TestSuiteQuery`, `TestExecutionRecordQuery`, `TestSuiteExecutionRecordQuery`, `TestResultQuery`, `TestSuiteResultQuery`, `TestDataQuery`, `KeywordQuery`, `TestEnvironmentQuery`, `BuildRecordQuery`, `BuildDefinitionQuery`. Cada um tem `queryBase` próprio (o último segmento do path é um nome interno do ETM, não deve ser fixado no código).
3. A resposta de uma query é RDF/XML com: um `rdf:Description` do próprio recurso de consulta (lista os membros em `oslc_qm:<tipo>` e tem o `rdf:type` `...Query`), um `rdf:Description` `oslc:ResponseInfo` (`oslc:totalCount`, `oslc:nextPage`) e **os membros inline**, cada um um `rdf:Description` completo (`dcterms:title`, `dcterms:identifier`, `rqm_qm:shortIdentifier`, `oslc:shortId`, `dcterms:created/modified/creator/contributor`, links). Os membros se reconhecem pelo `rdf:type` do domínio QM e NÃO pelo recurso de consulta nem pelo ResponseInfo.
4. O parâmetro de tamanho de página é `oslc_qm.pageSize`; `oslc:nextPage` já traz o token (`oslc.paging=true&rqm_qm.queryToken=...`) e deve ser seguido como veio. `totalCount` confere com a contagem da UI (187 casos de teste no projeto de referência).
5. Links no caso de teste: `oslc_qm:validatesRequirement` (→ RM), `oslc_qm:testsChangeRequest` (→ CCM), `oslc_qm:usesTestScript`; na execução: `oslc_qm:runsTestCase`, `oslc_qm:executesTestScript`, `rqm_qm:producesTestResult`, `rqm_qm:currentTestResult`.
6. A paginação reaproveita o que a W2 entregou (`query_resources`, mesma origem, ciclo, teto).

## Defeito
| # | Tool | Sintoma | Correção |
|---|---|---|---|
| D12 | `search_testcases` | Devolve um único item `raw_xml` com a página inteira; além disso consulta a capability errada (TestExecutionRecord) | Escolher a capability por `resourceType`, interpretar o RDF e devolver um item por membro |

## Tools
| Tool | Retorno |
|---|---|
| `list_qm_resources(project, type, query=None, page_size=50)` | Lista por tipo (os 14), com título, identificador, id curto (`rqm_qm:shortIdentifier`), estado e URI; paginação |
| `get_qm_resource(uri)` | Propriedades normalizadas + links (requisitos, work items, scripts, TCER, resultados) |
| `get_test_coverage(project)` | Para cada caso de teste: requisitos validados e work items testados (rastreabilidade) |
| `get_test_results(project, test_case=None)` | Resultados com veredito, pontos por status e TCER de origem |

## Critério de aceite (EARS)
- QUANDO `search_testcases` ou `list_qm_resources` for chamado, o sistema DEVE retornar um item por recurso, com URI, título e identificadores.
- O sistema DEVE pedir `application/rdf+xml` ao QM e NÃO DEVE depender de JSON.
- QUANDO o catálogo for lido, o sistema DEVE reconhecer service providers com atributos adicionais no elemento.
- QUANDO `get_qm_resource` for chamado, o sistema DEVE listar os links `validatesRequirement` e `testsChangeRequest` com as URIs de destino.
- QUANDO a listagem passar do tamanho de página, o sistema DEVE seguir a paginação até o limite pedido.
- ONDE a Reportable REST do ETM for usada, o sistema DEVE resolver o alias pelo feed de projetos, nunca usar o título como alias.

## Aceitação viva
Listar casos de teste de um projeto real e conferir a contagem com o `oslc:totalCount`; abrir um caso com link a requisito e a work item.

## Gates
G3 (respx: catálogo com atributos extras, página RDF com N membros, links) → G4 → G5 → aceitação viva.
