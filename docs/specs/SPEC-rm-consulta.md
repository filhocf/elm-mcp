# SPEC — RM: consulta com contexto de configuração e itens interpretados

**Status:** G0 feito (leitura viva 23/nov) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Onda:** W4
**Classe:** correção de defeito (achada na leitura viva) e pré-requisito de `SPEC-idempotencia`

## Evidência G0 (leitura viva, 23/nov/2026)
1. Consulta RM com `oslc.where` sem contexto de configuração responde 400 (`Undefined namespace prefix: dcterms` e, com prefixo, `localConfiguration and globalConfigurationUri are null`).
2. Funciona com **`oslc.prefix`** declarando os prefixos usados e o cabeçalho **`Configuration-Context: <URI da stream>`**. A stream vem de: service provider → `oslc_config:component` → componente (rdf+xml) → `oslc_config:configurations` → `rdfs:member` (a stream; o projeto de referência tem uma só).
3. A resposta é RDF/JSON (`{uri: {propriedade: [{type,value}]}}`) e hoje `search_requirements` devolve esse dict cru dentro de `items` (um único item gigante), como o D12 do QM.
4. Sem `oslc.select` a resposta traz só `rdf:type` por recurso; é preciso pedir `oslc.select=dcterms:title,dcterms:identifier,dcterms:modified,oslc:instanceShape` (e declarar `oslc` em `oslc.prefix`). A resposta traz também um recurso-contêiner (`.../rm/viewsnull`, com `rdfs:member`) e um `ResponseInfo` (`oslc:nextPage`, `dcterms:title` "Query Results: N" com o total) — nenhum dos dois é requisito. Fixture sanitizada: `tests/fixtures/rm_query_page.json` (398 requisitos no projeto de referência).
5. A escrita direta na stream funciona sem changeset (SPEC-ewm-delta fato 7). Descobrir a stream aqui não ativa o modo changeset (continua ADIADO).

## Tools
| Tool | Mudança |
|---|---|
| `search_requirements(project, query=None, limit=None)` | Passa a enviar `oslc.prefix` e `Configuration-Context` e a devolver um item por requisito: `uri`, `title`, `identifier`, `type` (instanceShape), `modified`, `folder` quando houver |

## Critério de aceite (EARS)
- QUANDO `search_requirements` for chamado com `query`, o sistema DEVE declarar em `oslc.prefix` os prefixos usados na consulta (dcterms, oslc, rdf, nav, jazz_rm) e enviar o `Configuration-Context` da stream do projeto.
- SE o projeto não tiver configuração descobrível (sem `oslc_config:component`), ENTÃO o sistema DEVE consultar sem o cabeçalho.
- SE houver mais de uma stream, ENTÃO o sistema DEVE usar a primeira listada e DEVE informar qual usou no retorno (`configuration`).
- O sistema DEVE enviar `oslc.select` com título, identificador, modificação e instanceShape, e DEVE obter o total do título "Query Results: N" do ResponseInfo quando não houver `oslc:totalCount`.
- QUANDO a resposta vier em RDF/JSON, o sistema DEVE devolver um item por requisito (recurso com `rdf:type` `oslc_rm#Requirement`), e NÃO DEVE devolver o dicionário cru.
- O sistema DEVE aplicar a paginação, o limite, a mesma origem e o diagnóstico de gate já existentes.

## Gates
G3 (respx: 400 sem contexto, 200 com cabeçalho, uma e várias streams, sem configuração, página com N requisitos) → G4 → G5 → aceitação viva (somente leitura: contar requisitos de uma consulta por título).
