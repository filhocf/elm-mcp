# SPEC — Correções CCM contra ELM 7.0.1

**Status:** PLANNED · **Release:** v0.5.1 (correção) · **Guarda-chuva:** `SPEC-ewm-delta` F1 · **ADR:** ADR-003
**Classe:** correção de defeito (observado no servidor de referência)

## Defeitos
| # | Tool | Sintoma observado | Correção |
|---|---|---|---|
| D1 | `list_types("ccm")` | Devolve títulos de propriedades ("Children", "Verified") | Listar as `oslc:CreationFactory` do service provider: título, id do tipo, URI da fábrica e do shape |
| D2 | `list_fields("ccm", tipo)` | `Type 'story' not found` | Resolver o tipo pelo resultado de D1 (id do tipo, título ou alias `task`/`story`) |
| D6 | `create_workitem`, `update_workitem`, `add_child_workitem` (`owner`) | 409 `Unknown attribute ownedBy` | Gravar o responsável em `dcterms:contributor` |
| D9 | `create_workitem` | 500 ao criar story pela fábrica genérica | POST na fábrica **do tipo**; propagar `oslc:message` do servidor no `ToolError` |
| D10 | `transition_workitem` | Busca ações em `{item}/rtc_cm:workflowActions` (não existe; o servidor devolve o próprio item); fallback faz PUT em `rtc_cm:state` (ignorado com 200) | Executar por `?_action=<id>`; reler o estado; aceitar id de ação completo, sufixo (`a27`) ou alias do perfil |
| D11 | `update_resource` | PUT do recurso inteiro mesclado (130+ campos) | PUT parcial `?oslc.properties=<props alteradas>` com `If-Match` |

## Critério de aceite (EARS)
- QUANDO `list_types("ccm", projeto)` for chamado, o sistema DEVE retornar um item por fábrica de criação, com título, identificador do tipo, URI da fábrica e URI do shape, e NÃO DEVE retornar propriedades de shape.
- QUANDO `list_fields("ccm", projeto, tipo)` receber o identificador ou o título de um tipo listado, o sistema DEVE retornar as propriedades do shape com ocorrência, somente-leitura e `allowedValues`.
- QUANDO `owner` for informado em criação ou atualização, o sistema DEVE gravá-lo em `dcterms:contributor`.
- QUANDO `create_workitem` for chamado, o sistema DEVE enviar o POST à fábrica de criação do tipo pedido.
- SE o servidor recusar uma escrita com `oslc:message`, ENTÃO o sistema DEVE levantar `ToolError` contendo essa mensagem.
- QUANDO `transition_workitem(id, action)` for chamado, o sistema DEVE executar a ação via `?_action=` com `If-Match`, reler o item e retornar o estado anterior e o novo.
- SE o estado relido for igual ao anterior, ENTÃO o sistema DEVE levantar `ToolError` dizendo que a ação não se aplica ao estado atual.
- O sistema NÃO DEVE gravar estado por PUT em `rtc_cm:state`.
- QUANDO uma atualização for feita, o sistema DEVE enviar só as propriedades alteradas via `oslc.properties`, com `If-Match`; SE o servidor responder 412, ENTÃO DEVE levantar `ToolError` de conflito.

## Aceitação viva (servidor de referência, item descartável)
Criar tarefa e story pela fábrica do tipo com os obrigatórios do perfil; atribuir responsável; transicionar story por 2 ações
válidas e 1 inválida (esperado: erro "não se aplica"); atualizar estimativa com ETag obsoleto (esperado: 412 → `ToolError`).

## Gates
G0 (evidência no guarda-chuva) → G3 (respx: fábricas, shape, 200-sem-efeito, 412, 403 com `oslc:message`) → G4 → G5 → aceitação viva.
