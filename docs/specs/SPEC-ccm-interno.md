# SPEC — Folha de horas e anexo: leitura por OSLC; escrita por API interna ADIADA

**Status:** PARCIAL (G0 feito em leitura viva 23/nov) · **Release:** v0.6.0 (leitura) · **Guarda-chuva:** `SPEC-ewm-delta` F5 · **Depende de:** `SPEC-ccm-correcoes`

## Contexto (verificado no servidor de referência)
- Folha de horas: leitura por OSLC 2.0 (`rtc_cm:timeSheet` → `rtc_cm:entry`; ver `SPEC-horas`). Escrita por OSLC responde 405. Só funciona pelo
  serviço interno `IWorkItemRestService/workItem2` com `updateLinks` (`addLink`/`updateLink`/`removeLink` do link `timeSheetEntry`)
  e o total em `timeSpent`.
- Anexo: upload só pelo serviço interno `IAttachmentRestService/attachment?projectId={pa}` (multipart; resposta HTML com JSON em
  `<textarea>`). O link ao item e o download funcionam por OSLC.
- Serviços internos não têm suporte da IBM e podem mudar entre versões.

## Evidência G0 de anexos (leitura viva, 23/nov/2026)
1. O work item lista anexos em `rtc_cm:com.ibm.team.workitem.linktype.attachment.attachment`: lista de `{rdf:resource, dcterms:title, rdf:type: rtc_cm:Attachment}`; o `dcterms:title` vem prefixado com o identificador (`"<id>: <nome>"`).
2. `GET <uri do anexo>` com `Accept: application/json` devolve os metadados (`dcterms:title`, `dcterms:format`, `dcterms:creator`, `dcterms:created/modified`, `dcterms:identifier`).
3. O conteúdo vem do MESMO URI com `Accept: */*` (HTTP 200, `Content-Disposition: attachment; filename*=utf-8''...`, bytes íntegros). Com `Accept: image/png` ou `application/octet-stream` responde 406.
4. Fixture sanitizada: `tests/fixtures/ccm_attachment.json`.

## Decisão do gerente (Gate -1): escrita por serviço interno ADIADA
`log_time`, `remove_time` e `add_attachment` ficam FORA da v0.6.0. Motivos: (a) a escrita no ALM de referência está suspensa pelo mantenedor, então nada pode ser verificado ao vivo; (b) os serviços são internos, sem documentação nem suporte, e o formato dos payloads (`workItem2`/`updateLinks`, multipart) só existe como inspiração numa base de terceiros sem autorização de reuso (decisão do mantenedor, 22/nov); (c) lançar horas é ação com efeito administrativo real. Implementar sem evidência seria chute em dado de produção. Condição para destravar: permissão de escrita em item descartável + captura do payload no navegador/proxy + OK do mantenedor sobre o reuso.

## Tools (v0.6.0)
`list_time(id)` (ver `SPEC-horas`), `get_hours` (ver `SPEC-horas`), `list_attachments(id)` e `download_attachment(id, attachment, dest)` (OSLC, sem flag).

## Critério de aceite (EARS)
- QUANDO `list_attachments` for chamado, o sistema DEVE devolver, por anexo, `uri`, `name` (sem o prefixo do identificador), `identifier`, `format`, `creator`, `created` e `size` quando houver.
- QUANDO `download_attachment` for chamado, o sistema DEVE pedir o conteúdo com `Accept: */*` ao URI do anexo, gravar os bytes em `dest` (diretório ou arquivo), devolver o caminho e o tamanho, e NÃO DEVE sobrescrever um arquivo existente sem `overwrite=True`.
- O sistema DEVE usar só o nome base do arquivo vindo do servidor e NÃO DEVE gravar fora de `dest` (path traversal: `../`, `/`, nomes vazios).
- O sistema DEVE aplicar mesma origem e diagnóstico de gate, e limitar o tamanho baixado a `ELM_MAX_DOWNLOAD_MB` (padrão 50), recusando acima.
- `list_attachments` e `download_attachment` NÃO DEPENDEM de flag.

## Gates
G3 (respx: metadados; 406 com Accept errado; bytes íntegros; nome malicioso `../x`; arquivo existente; limite de tamanho; anexo de outra origem) → G4 → G5 → aceitação viva (somente leitura: listar anexos de um item real e baixar um arquivo pequeno para comparar o cabeçalho PNG).
