# SPEC — Horas: leitura da folha e agregação por pessoa e por item

**Status:** DONE (G0 leitura viva 23/nov; G3/G4 em W5; aceitação viva pendente) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Onda:** W5 · **Depende de:** `SPEC-paginacao-sessao`, `SPEC-ccm-correcoes`
**Origem:** caso de uso real do backlog: IB com 99 tarefas filhas, `timeSpent` nulo em 53, ~130 entradas de folha lidas uma a uma com 130 chamadas de `get_requirement`.

## Evidência G0 (leitura viva, 23/nov/2026)
1. `rtc_cm:timeSpent` e `rtc_cm:estimate` do work item são **milissegundos** (inteiros; 129600000 = 36 h). Podem ser nulos.
2. `oslc.where=rtc_cm:timeSpent>0` funciona e filtra no servidor.
3. `rtc_cm:timeSheet` (URI própria do work item, tipo `rtc_cm:TimeSheet`) devolve `rtc_cm:entry`: uma lista de **referências** (`rdf:resource`) a `TimeSheetEntry`, sem os campos. Cada entrada precisa de um GET (JSON) e traz `rtc_cm:timeSpent` (ms), `rtc_cm:startDate`, `rtc_cm:timeCode`/`rtc_cm:timeCodeId`, `dcterms:creator` (URI do usuário) e `rtc_cm:workItem`.
4. Fixtures sanitizadas: `tests/fixtures/ccm_wi.json`, `ccm_sheet.json`, `ccm_entry.json`.

## Tools
| Tool | Retorno |
|---|---|
| `list_time(id)` | Entradas do work item: `{entry_uri, person, date, hours, time_code}` + `total_hours` |
| `get_hours(item_ids=None, plan=None, group_by="person", max_entries=500)` | Horas por pessoa ou por item, para os work items dados e seus filhos diretos (mesma seleção de `get_plan_items`) |

## Critério de aceite (EARS)
- O sistema DEVE converter milissegundos em horas (2 casas decimais) e tratar `timeSpent` nulo como 0, sem levantar erro.
- QUANDO `get_hours` for chamado, o sistema DEVE ler as entradas da folha com concorrência limitada (no máximo 8 requisições simultâneas), mesma origem e reautenticação já existentes.
- QUANDO `group_by="person"`, o sistema DEVE somar as horas por `dcterms:creator` e informar `items_counted` e `entries_read`; QUANDO `group_by="item"`, DEVE somar por work item e incluir a estimativa em horas.
- SE o total de entradas passar de `max_entries`, ENTÃO o sistema DEVE parar de ler, devolver o parcial com `partial: true` e o número de entradas não lidas, e NÃO DEVE levantar erro.
- SE a leitura de uma entrada falhar, ENTÃO o sistema DEVE registrar a entrada em `errors` e seguir com as demais; o total DEVE refletir só as entradas lidas.
- QUANDO um work item tiver `timeSpent` mas nenhuma entrada, o sistema DEVE somar o `timeSpent` do item em `group_by="item"` e DEVE sinalizar `without_entries`.
- O sistema NÃO DEVE retornar identificadores de usuário além da URI/nome já presente nas entradas.
- O sistema DEVE respeitar `readOnlyHint` (leitura) e NÃO DEVE depender de `ELM_ENABLE_INTERNAL_API`.

## Gates
G3 (respx: 2 entradas de pessoas diferentes; `timeSpent` nulo; limite de entradas; falha de uma entrada; concorrência no máximo 8; item sem entradas; conversão ms→h) → G4 → G5 → aceitação viva (somente leitura: um IB real, conferir `sum(entries) == timeSpent` por tarefa).
