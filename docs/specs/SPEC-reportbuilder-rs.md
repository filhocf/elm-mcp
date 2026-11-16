# SPEC — Relatórios (Report Builder / RS) via REST

**Status:** PLANNED (G0 parcial feito) · **Release:** v0.8.0 · **RFC-mãe:** RFC-001 §6
**Classe:** entregável de produto — pedido do time de gestão

## Meta
Expor para agentes e gestão os **relatórios já montados no Report Builder**, sem tocar o banco do ALM.

## Evidência (servidor de referência, Report Builder 7.0.1 SR1)
- **Autenticação:** a sessão do JTS por formulário não basta. `/rs/*` responde 401 com o header `x-jazz-web-oauth-url`. Seguir essa URL
  com a sessão JTS ativa completa o OAuth "web" do Jazz (redirecionamentos 302/307) e o callback cria a sessão do RS. Basic auth: 401.
- **Execução:** `GET /rs/query/{id}/dataservice?report={id}&limit=N&basicAuthenticationEnabled=true` devolve XML (`<results>`).
  É a URL REST que a interface exporta para cada relatório.
- **Custo:** execuções passaram de 120 s. Executar relatório é operação cara no servidor.
- **Listagem:** não foi encontrado endpoint REST de listagem. `/rs/reports` devolve a interface HTML mesmo pedindo JSON.

## Escopo
- Autenticação RS no `auth.py`: detectar `x-jazz-web-oauth-url`, completar o fluxo e reaproveitar a sessão.
- `run_report(report_id, limit=100, timeout=300)`: executa pelo dataservice e devolve linhas normalizadas (colunas e valores).
- `list_reports()`: **condicionado** a achar o endpoint de listagem (ou o export de definições). Enquanto não houver, os relatórios
  vêm de configuração (`reports` no perfil: id, nome, descrição), alimentada pelo time de gestão a partir da URL REST de cada um.

## Restrições (não negociáveis)
- O sistema NÃO DEVE acessar o banco transacional do Jazz.
- O sistema NÃO DEVE varrer ids de relatório.
- Ordem de fonte: Report Builder (dataservice) → Reportable REST da aplicação → LQE/SPARQL → Data Warehouse (fallback).

## Critério de aceite (EARS)
- QUANDO uma chamada ao RS receber 401 com `x-jazz-web-oauth-url`, o sistema DEVE completar o OAuth do Jazz e repetir a chamada uma única vez.
- QUANDO `run_report` for chamado com id válido, o sistema DEVE retornar no máximo `limit` linhas, com nomes de coluna.
- O sistema DEVE aplicar timeout configurável e, SE estourar, ENTÃO DEVE levantar `ToolError` dizendo que o relatório é pesado e sugerindo `limit` menor.
- QUANDO `list_reports` for chamado sem endpoint de listagem disponível, o sistema DEVE retornar os relatórios configurados no perfil.
- SE o Report Builder não estiver instalado (404 em `/rs/application-about`), ENTÃO o sistema DEVE sinalizar com `ToolError` claro.

## A confirmar (G0)
1. Endpoint de listagem ou de exportação de definições (`reportdefinition?action=export&selections=`).
2. Formatos do dataservice (CSV/JSON além de XML) e paginação.
3. Ids e URLs REST dos relatórios do time de gestão.

## Gates
G0 (itens a confirmar) → G3 (mock do 401+OAuth, do dataservice e do timeout) → G4 → G5 → aceitação viva com 1 relatório real e `limit` pequeno.
