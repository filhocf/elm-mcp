# SPEC — QM (ETM): leitura correta e completa

**Status:** PLANNED · **Release:** v0.6.0 · **Guarda-chuva:** `SPEC-ewm-delta` F6 · **Antecede:** `SPEC-qm-write`

## Contexto (verificado no servidor de referência, ETM 7.0.1 SR1)
- O ETM responde só RDF/XML (JSON dá 406).
- Há 14 query capabilities por projeto (TestPlan, TestCase, TestScript, TestScriptStep, TestSuite, TestExecutionRecord,
  TestSuiteExecutionRecord, TestResult, TestSuiteResult, TestData, Keyword, TestEnvironment, BuildRecord, BuildDefinition).
- O catálogo de service providers usa atributos extras no elemento; parser ingênuo não encontra os projetos.
- Links cross-domínio estão nas propriedades `oslc_qm:validatesRequirement` (→ RM) e `oslc_qm:testsChangeRequest` (→ CCM).
- O ETM expõe também a sua Reportable REST (`IIntegrationService`), endereçada por **alias** de projeto, que pode diferir do título.

## Defeito
| # | Tool | Sintoma | Correção |
|---|---|---|---|
| D12 | `search_testcases` | Devolve um único item `raw_xml` com a página inteira | Interpretar o RDF e devolver um item por recurso |

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
