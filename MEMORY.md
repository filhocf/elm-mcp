# MEMORY.md — elm-mcp

## Estado Atual (nov/2026)

- **Versão**: 0.4.0 publicada; onda W2 (colaboração CCM, v0.6.0) em desenvolvimento na branch `feat/w2-colaboracao`, ainda sem release
- **Tools**: 25 (14 de leitura, 11 de escrita); lista completa e assinaturas em `docs/TOOLS.md`
- **Repo**: https://github.com/filhocf/elm-mcp
- **Status**: ✅ Testado end-to-end contra ELM real

## Feito

- 25 tools (ver `docs/TOOLS.md`). Na origem (v0.1.0) eram 6: list_projects, search_requirements, get_requirement, list_workitems, get_workitem, search_testcases
- W2: add_comment, list_comments, link_workitems, unlink_workitems, set_field, list_workflow, filtros de list_workitems, paginação por `oslc:nextPage` ({"items","truncated"} em list_workitems, search_requirements, search_testcases)
- W2 (loop 2 do G5): toda URL seguida de resposta do servidor fica no mesmo scheme/host/porta de `ELM_URL`; paginação com teto de 100 páginas e detecção de ciclo; `add_comment` envia o texto escapado (HTML) e confirma pelo id novo
- Auth: form-based Jazz com cookies (httpx AsyncClient singleton)
- Config: env vars + ~/.elm_creds.json (base64 password)
- CLI: `elm-mcp login` (setup interativo) + `elm-mcp serve` (MCP stdio)
- OSLC: discovery via rootservices → catalog → ServiceProvider → QueryCapability
- Namespaces: corrigidos para ELM 7.0.3 (Discovery 1.0 para RM, OSLC Core para CCM/QM)
- RM: endpoint /views com projectURL (DOORS Next não expõe QueryCapability padrão)
- QM: fallback XML quando JSON retorna 406
- CCM: derivação de /services a partir da URL do catalog
- Testes: suíte local sem rede (respx; a rede real é bloqueada pelo conftest)
- CI: GitHub Actions (lint + test + coverage) + CodeQL + Dependabot + Publish
- Gemini Code Assist: ativo
- Conformidade DEVELOPMENT-STANDARDS.md: ✅

## Teste End-to-End (18/mai/2026, DNBSCDC289)

| Domínio | Projetos | Query |
|---------|:--------:|:-----:|
| CCM (RTC) | 7 | 50 work items MIR ✅ |
| RM (DOORS Next) | 3 | Requirements MIR ✅ |
| QM (ETM) | 2 | Test cases MIR ✅ |

## Pendente

- [ ] Integrar no mcp.json local (substituir elm_poc antigo)
- [ ] Igualar nas 3 máquinas (socrates, sirdata)
- [ ] Aumentar coverage (testes para novos paths RM/QM)
- [ ] Tag v0.1.0 + release PyPI
- [ ] Testar oslc.where queries (filtros)

## Decisões

- Substituiu `elm_poc` (192 linhas, só CCM, baseado em elmclient)
- httpx em vez de requests (async nativo)
- FastMCP em vez de MCP SDK raw
- Sem elmclient como dependência
- RM usa /views endpoint (Discovery 1.0, não OSLC Core QueryCapability)
- QM aceita só XML (fallback automático quando JSON dá 406)
- Coverage threshold 60% (cli.py excluído, código novo testado e2e)
- ELM URL configured via env var or ~/.elm_creds.json (no hardcoded default)
