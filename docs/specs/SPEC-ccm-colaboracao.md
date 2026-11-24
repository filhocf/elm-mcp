# SPEC — Colaboração CCM: comentários, links, campos e consultas

**Status:** G4 GREEN (respx, W2); aceitação viva PARCIAL feita em 24/nov/2026 (ver 'Resultado da aceitação viva') · **Release:** v0.6.0 · **Guarda-chuva:** `SPEC-ewm-delta` F3 · **Depende de:** `SPEC-ccm-correcoes`

## Tools novas ou ampliadas
| Tool | Como (verificado no servidor de referência) |
|---|---|
| `add_comment(id, text)` | POST OSLC CM 1.0 em `/ccm/oslc/workitems/{id}/rtc_cm:comments`, `Content-Type: application/x-oslc-cm-change-request+json`, corpo `{"dc:description": text}`, sem `OSLC-Core-Version` (a 2.0 responde 405) |
| `list_comments(id)` | OSLC 2.0: `oslc:discussedBy` → `oslc:comment` (identificador, autor, data, texto) |
| `link_workitems(source, target, link_type)` / `unlink_workitems(...)` | PUT parcial na propriedade `rtc_cm:com.ibm.team.workitem.linktype.<tipo>` com `If-Match`; remoção reenvia a lista sem o alvo. Tipos: `parent`, `blocks`, `depends_on`, `related`, `duplicate_of`, `resolves` |
| `set_field(id, field, value)` | PUT parcial. Para enum, `value` pode ser título ou id do literal; resolver por `/oslc/enumerations/{pa}/{enum}` e validar contra `allowedValues` |
| `list_workitems` ampliado | Filtros `type`, `state`, `owner`, `planned_for` montados em `oslc.where`; paginação por `oslc:nextPage` |
| `list_workflow(project, type)` | Estados com título e grupo (`/oslc/workflows/{pa}/states/{wf}`, descobertos pelo `allowedValues` da propriedade `state` da shape; sem o documento de títulos, devolve só os ids) e ids das ações (`/actions/{wf}`) |

## Critério de aceite (EARS)
- QUANDO `add_comment` for chamado, o sistema DEVE publicar o comentário e confirmar que ele aparece em `list_comments`; SE não aparecer, ENTÃO DEVE levantar `ToolError` sem repetir o POST.
- QUANDO `link_workitems` for chamado, o sistema DEVE criar o link e confirmar na releitura; `unlink_workitems` DEVE remover só aquele alvo e preservar os demais links.
- QUANDO `set_field` receber o título de um literal, o sistema DEVE resolver o id pela enumeração do projeto; SE o valor não existir, ENTÃO DEVE recusar listando os válidos.
- SE um atributo obrigatório receber o literal de "não designado", ENTÃO o sistema DEVE avisar que o servidor trata esse valor como vazio.
- QUANDO `list_workitems` receber filtros, o sistema DEVE aplicá-los no servidor e seguir a paginação até o limite pedido.

## Aceitação viva
Em itens descartáveis: comentar e reler; criar e remover `depends_on` e `related`; re-parent; gravar pontos por título ("5 pts").

### Resultado da aceitação viva (24/nov/2026, servidor de referência, itens descartáveis; as tarefas e o IB foram cancelados ao fim)
- **Confirmado:** `add_comment` + `list_comments` (o comentário volta com `id`, `author`, `created`, `text`); `link_workitems` e `unlink_workitems` com `related` e `depends_on` (vincular, repetir devolve `changed: false`, desvincular, desvincular de novo é erro); `set_field` de campo enumerado por id (`complexity` = `5`, valor gravado como `{"rdf:resource": ...}`); criação de tarefa com `parent_id` (o IB lista a filha em `children`); `list_workitems(type=...)`.
- **Achado:** `list_comments` devolve o texto como o servidor guarda, escapado em HTML (`&lt;b&gt;`): issue #81.
- **NÃO exercitado ao vivo:** re-parent (`parent`), `blocks`, `duplicate_of`, `resolves`, pontos por título (`5 pts`), filtro `planned_for`.

## Gates
G3 (respx: 405 na 2.0, 201 na 1.0, preservação de links) → G4 → G5 → aceitação viva.

## Nomes verificados no servidor real (7.0.1, leitura)
Prefixo `rtc_cm:com.ibm.team.workitem.linktype.`: `parent` → `parentworkitem.parent` (inverso `parentworkitem.children`);
`blocks` → `blocksworkitem.blocks`; `depends_on` → `blocksworkitem.dependsOn`; `related` → `relatedworkitem.related`;
`duplicate_of` → `duplicateworkitem.duplicateOf` (inverso `duplicates`); `resolves` → `resolvesworkitem.resolves`
(inverso `resolvedBy`). Valores: lista de `{"rdf:resource", "dcterms:title"}`. Discussão: `oslc:discussedBy` →
`.../oslc/workitems/<id>/rtc_cm:comments`, que devolve `{"oslc:comment": [...], "oslc:discussionAbout": ...}`.
Filtros em `oslc.where` (verificado em leitura): `rtc_cm:type="<id do tipo>"` funciona com id curto/alias (`task`) ou completo;
`dcterms:type="<título>"` devolve 0 resultados (não filtra); `rtc_cm:state="<id completo do estado>"` funciona, com título devolve 0;
`oslc_cm:status` dá HTTP 400 (`Unknown attribute id`); `oslc_cm:closed=true` funciona; `dcterms:contributor=<URI do usuário>`
funciona (com aspas e username dá HTTP 400). `rtc_cm:plannedFor=<URI da iteração>` NÃO foi verificado.
`list_workflow`: na shape do tipo, a propriedade `state` aponta para `.../shapes/workitems/<tipo>/property/internalState/allowedValues`
(sem `/oslc/workflows`); esse documento lista as URIs `/oslc/workflows/{pa}/states/{wf}/{estado}`, de onde saem área e workflow.
O `list_workitems` resolve `type` (título/alias/id) pela descoberta de tipos e aceita `state` como id; um título de estado só
vale junto de `type` (resolvido pelo workflow), senão erro pedindo o id (use `list_workflow`).

## A confirmar na aceitação viva (to be confirmed in live acceptance)
- ~~Corpo de um comentário individual e a resposta do POST~~: confirmado em 24/nov/2026 (volta `id`, `author`, `created`, `text`; o texto vem escapado em HTML, issue #81).
- Formato do documento `allowedValues` da enumeração usado por `set_field`: o valor por id foi confirmado em 24/nov/2026; os rótulos vêm da enumeração (#77, mergeado); o título do literal "Unassigned" segue sem verificação.
- Que o serviço de consulta aceita `rtc_cm:plannedFor=<URI>` em `oslc.where` (os demais filtros foram verificados).
- `parent` substitui o pai atual (um item tem um só pai): confirmar no re-parent.
