# SPEC — Correções CCM contra ELM 7.0.1

**Status:** G1 · **Release:** v0.5.1 (correção) · **Guarda-chuva:** `SPEC-ewm-delta` F1 · **ADR:** ADR-003
**Classe:** correção de defeito (observado no servidor de referência)

## Defeitos
| # | Tool | Sintoma observado | Correção |
|---|---|---|---|
| D1 | `list_types("ccm")` | Devolve títulos de propriedades ("Children", "Verified") | Listar as `oslc:CreationFactory` do service provider: título, id do tipo, URI da fábrica e do shape |
| D2 | `list_fields("ccm", tipo)` | `Type 'story' not found` | Resolver o tipo pelo resultado de D1 (id do tipo, título ou alias `task`/`story`) |
| D6 | `create_workitem` (e `add_child_workitem`, que delega) (`owner`) | 409 `Unknown attribute ownedBy` | Gravar o responsável em `dcterms:contributor` (`update_workitem` já usa o campo certo) |
| D9 | `create_workitem` | 500 ao criar story pela fábrica genérica | POST na fábrica **do tipo**; propagar `oslc:message` do servidor no `ToolError` |
| D10 | `transition_workitem` | Busca ações em `{item}/rtc_cm:workflowActions` (não existe; o servidor devolve o próprio item); fallback faz PUT em `rtc_cm:state` (ignorado com 200) | Executar por `?_action=<id>`; reler o estado. Resolver `action` contra as ações do workflow do item (ver abaixo). Aliases de perfil ficam para `SPEC-perfil-template` |

### Resolução de `action` em `transition_workitem` (compatível com o contrato atual)
O workflow e a project area saem da URI de `rtc_cm:state` do item (`…/oslc/workflows/{pa}/states/{wf}/{estado}`). As ações vêm de
`GET /ccm/oslc/workflows/{pa}/actions/{wf}` (só ids). O valor de `action` é resolvido nesta ordem:
1. id completo igual;
2. id terminando em `.{action}` (ex.: `a27`);
3. id contendo `action` como substring, se houver **exatamente um** (mantém `start`, `resolve`, `close`, `reopen` funcionando em
   templates cujos ids carregam a palavra, ex.: `…action.startWorking`).
Zero ou mais de um candidato: `ToolError` listando os ids disponíveis. O docstring, `docs/TOOLS.md` e a skill `elm-workitems`
passam a dizer que `action` é o id (ou sufixo) da ação do workflow, e que as quatro palavras valem só onde o template as tem.
| D11 | `update_resource` | PUT do recurso inteiro mesclado (130+ campos) | PUT parcial `?oslc.properties=<props alteradas>` com `If-Match` |
| D13 | `create_workitem` sem `filed_against` | 500 (loop 1) e depois 403 `Atendido por needs to be set` (loop 2): o `oslc:defaultValue` de `filedAgainst` no shape é a categoria raiz "Não designado", que o processo trata como vazia; não há flag OSLC que a identifique (só o título localizado) | **Decisão do mantenedor (23/nov):** não escolher categoria automaticamente. Sem `filed_against`, NÃO enviar `rtc_cm:filedAgainst` (vale o default do servidor); remover `_find_default_category` e o fallback "primeiro `allowedValue`". Se o servidor recusar com `oslc:message` sobre categoria, o `ToolError` DEVE acrescentar a dica de usar `list_fields` (mostra `allowedValues` de `filedAgainst`). Default real por PA vai para o perfil (`SPEC-perfil-template`, F4) |

### Formato real do shape no servidor de referência (D2, achado na aceitação viva do loop 1)
O shape de CCM não usa elementos `<oslc:Property>`. Cada propriedade é um bloco
`<rdf:Description rdf:about="…/shapes/workitems/{tipo}/property/{id}">` com `<rdf:type rdf:resource="http://open-services.net/ns/core#Property"/>`,
`oslc:name`, `oslc:propertyDefinition`, `oslc:occurs`, `oslc:readOnly`, `oslc:defaultValue`, `oslc:allowedValues`, `dcterms:title`
(com `rdf:parseType="Literal"`) em qualquer ordem. O `rdf:about` da propriedade pode diferir do nome (ex.: `…/property/category` para `filedAgainst`).
O parser DEVE aceitar os dois formatos (elemento `oslc:Property` e `rdf:Description` tipado), e RM/QM não podem regredir.

## Critério de aceite (EARS)
- QUANDO `list_types("ccm", projeto)` for chamado, o sistema DEVE retornar um item por fábrica de criação, com título, identificador do tipo, URI da fábrica e URI do shape, e NÃO DEVE retornar propriedades de shape.
- QUANDO `list_fields("ccm", projeto, tipo)` receber o identificador ou o título de um tipo listado, o sistema DEVE retornar as propriedades do shape com ocorrência, somente-leitura e `allowedValues`, nos dois formatos de shape descritos acima.
- SE o shape tiver propriedades e o parser não extrair nenhuma, ENTÃO o sistema DEVE levantar `ToolError` em vez de devolver listas vazias.
- QUANDO `create_workitem` for chamado sem `filed_against`, o sistema NÃO DEVE enviar `rtc_cm:filedAgainst` (vale o default do servidor) nem escolher categoria automaticamente (D13).
- QUANDO `owner` for informado em criação ou atualização, o sistema DEVE gravá-lo em `dcterms:contributor`.
- QUANDO `create_workitem` for chamado, o sistema DEVE enviar o POST à fábrica de criação do tipo pedido.
- SE o servidor recusar uma escrita com `oslc:message`, ENTÃO o sistema DEVE levantar `ToolError` contendo essa mensagem.
- QUANDO `transition_workitem(id, action)` for chamado, o sistema DEVE resolver `action` pela ordem definida acima, executar via `?_action=` com `If-Match`, reler o item e retornar o estado anterior e o novo.
- SE `action` não resolver para exatamente uma ação do workflow do item, ENTÃO o sistema DEVE levantar `ToolError` listando as ações disponíveis, sem enviar escrita.
- SE o estado relido for igual ao anterior, ENTÃO o sistema DEVE levantar `ToolError` dizendo que a ação não se aplica ao estado atual.
- O sistema NÃO DEVE gravar estado por PUT em `rtc_cm:state`.
- QUANDO uma atualização for feita, o sistema DEVE enviar só as propriedades alteradas via `oslc.properties`, com `If-Match`; SE o servidor responder 412, ENTÃO DEVE levantar `ToolError` de conflito.

- QUANDO `filed_against` não for informado, o sistema NÃO DEVE enviar `rtc_cm:filedAgainst` nem consultar categorias.
- SE o servidor recusar a criação com `oslc:message` que cite a categoria, ENTÃO o `ToolError` DEVE conter o texto do servidor e a dica de consultar `list_fields` (`filedAgainst`).
- QUANDO `filed_against` for informado, o sistema DEVE enviá-lo como está.

## Aceitação viva (servidor de referência, item descartável)
Criar tarefa e story pela fábrica do tipo com os obrigatórios do perfil; atribuir responsável; transicionar story por 2 ações
válidas e 1 inválida (esperado: erro "não se aplica"); atualizar estimativa com ETag obsoleto (esperado: 412 → `ToolError`).

## Gates
G0 (evidência no guarda-chuva) → G3 (respx: fábricas, shape, 200-sem-efeito, 412, 403 com `oslc:message`) → G4 → G5 → aceitação viva.
