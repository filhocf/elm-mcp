# SPEC — Folha de horas e upload de anexo (API interna, atrás de flag)

**Status:** PLANNED · **Release:** v0.6.0 · **Guarda-chuva:** `SPEC-ewm-delta` F5 · **Depende de:** `SPEC-ccm-correcoes`

## Contexto (verificado no servidor de referência)
- Folha de horas: leitura por OSLC 2.0 (`rtc_cm:timeSheet` → `rtc_cm:entry`). Escrita por OSLC responde 405. Só funciona pelo
  serviço interno `IWorkItemRestService/workItem2` com `updateLinks` (`addLink`/`updateLink`/`removeLink` do link `timeSheetEntry`)
  e o total em `timeSpent`.
- Anexo: upload só pelo serviço interno `IAttachmentRestService/attachment?projectId={pa}` (multipart; resposta HTML com JSON em
  `<textarea>`). O link ao item e o download funcionam por OSLC (PUT parcial na propriedade de anexo; GET na URI do anexo).
- Serviços internos não têm suporte da IBM e podem mudar entre versões.

## Tools
`list_time(id)` (OSLC), `log_time(id, hours, date, time_code)`, `remove_time(id, date, time_code)`,
`list_attachments(id)` e `download_attachment(id, attachment, dest)` (OSLC), `add_attachment(id, path)` (upload interno + link OSLC).

## Critério de aceite (EARS)
- O código que usa serviço interno DEVE ficar em módulo próprio e só DEVE ser exposto com `ELM_ENABLE_INTERNAL_API=1`.
- A documentação DEVE dizer que esses serviços não têm suporte do fornecedor.
- QUANDO `log_time` for chamado, o sistema DEVE lançar ou atualizar a entrada do dia do usuário autenticado e confirmar por releitura OSLC; `remove_time` DEVE afetar só entradas do próprio usuário.
- SE o servidor recusar (precondição), ENTÃO o sistema DEVE levantar `ToolError` com a mensagem.
- QUANDO `add_attachment` for chamado, o sistema DEVE fazer o upload, criar o link por OSLC e confirmar na releitura.
- QUANDO `download_attachment` for chamado, o sistema DEVE gravar o arquivo íntegro no destino.
- `list_time`, `list_attachments` e `download_attachment` NÃO DEVEM depender da flag (são OSLC).

## Aceitação viva
Em item descartável: lançar 1 h, reler, remover, reler (zero); anexar arquivo pequeno, listar, baixar e comparar bytes.
