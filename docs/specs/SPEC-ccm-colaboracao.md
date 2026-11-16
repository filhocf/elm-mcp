# SPEC — Colaboração CCM: comentários, links, campos e consultas

**Status:** PLANNED · **Release:** v0.6.0 · **Guarda-chuva:** `SPEC-ewm-delta` F3 · **Depende de:** `SPEC-ccm-correcoes`

## Tools novas ou ampliadas
| Tool | Como (verificado no servidor de referência) |
|---|---|
| `add_comment(id, text)` | POST OSLC CM 1.0 em `/ccm/oslc/workitems/{id}/rtc_cm:comments`, `Content-Type: application/x-oslc-cm-change-request+json`, corpo `{"dc:description": text}`, sem `OSLC-Core-Version` (a 2.0 responde 405) |
| `list_comments(id)` | OSLC 2.0: `oslc:discussedBy` → `oslc:comment` (identificador, autor, data, texto) |
| `link_workitems(source, target, link_type)` / `unlink_workitems(...)` | PUT parcial na propriedade `rtc_cm:com.ibm.team.workitem.linktype.<tipo>` com `If-Match`; remoção reenvia a lista sem o alvo. Tipos: `parent`, `blocks`, `depends_on`, `related`, `duplicate_of`, `resolves` |
| `set_field(id, field, value)` | PUT parcial. Para enum, `value` pode ser título ou id do literal; resolver por `/oslc/enumerations/{pa}/{enum}` e validar contra `allowedValues` |
| `list_workitems` ampliado | Filtros `type`, `state`, `owner`, `planned_for` montados em `oslc.where`; paginação por `oslc:nextPage` |
| `list_workflow(project, type)` | Estados com título e grupo (`/oslc/workflows/{pa}/states/{wf}`) e ids das ações (`/actions/{wf}`) |

## Critério de aceite (EARS)
- QUANDO `add_comment` for chamado, o sistema DEVE publicar o comentário e confirmar que ele aparece em `list_comments`; SE não aparecer, ENTÃO DEVE levantar `ToolError` sem repetir o POST.
- QUANDO `link_workitems` for chamado, o sistema DEVE criar o link e confirmar na releitura; `unlink_workitems` DEVE remover só aquele alvo e preservar os demais links.
- QUANDO `set_field` receber o título de um literal, o sistema DEVE resolver o id pela enumeração do projeto; SE o valor não existir, ENTÃO DEVE recusar listando os válidos.
- SE um atributo obrigatório receber o literal de "não designado", ENTÃO o sistema DEVE avisar que o servidor trata esse valor como vazio.
- QUANDO `list_workitems` receber filtros, o sistema DEVE aplicá-los no servidor e seguir a paginação até o limite pedido.

## Aceitação viva
Em itens descartáveis: comentar e reler; criar e remover `depends_on` e `related`; re-parent; gravar pontos por título ("5 pts").

## Gates
G3 (respx: 405 na 2.0, 201 na 1.0, preservação de links) → G4 → G5 → aceitação viva.
