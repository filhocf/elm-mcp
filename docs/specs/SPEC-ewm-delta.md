# SPEC — Delta de funcionalidades (guarda-chuva)

**Status:** PLANNED (G0 feito: verificação no servidor de referência) · **RFC-mãe:** RFC-001 §5 · **ADR:** ADR-003
**Classe:** guarda-chuva. Cada fatia tem SPEC atômica própria e vira um PR.

## Meta

Tornar o elm-mcp correto e completo contra uma instalação ELM 7.0.1 customizada, usando o padrão OSLC do ELM como base e
isolando o que é interno ou específico de instalação. O ponto de partida foi um cliente MCP de equipe em produção, com 38 tools
(10 já cobertas, 9 em parte, 19 ausentes no elm-mcp), e a verificação de leitura e escrita no servidor de referência.

## Evidência (G0)
Verificação feita no servidor de referência (ELM 7.0.1 SR1), com itens descartáveis. Detalhe e reprodução:
`conhecimentos-de-ia/ferramentas/elm-mcp/testes-alm-dtp-leitura-2026-11-16.md`, `testes-alm-dtp-escrita-mir-2026-11-16.md`,
`docs-oficiais-elm-recursos.md` e `delta-cadu-ia-ewm-rm.md` (repositório de pesquisa do mantenedor).

Fatos que mudam o desenho:
1. A customização vive no **template de processo**: project areas do mesmo template têm tipos, propriedades e literais idênticos.
2. O shape OSLC anuncia os campos obrigatórios e `allowedValues`; `/oslc/enumerations/{pa}/{enum}` dá id e título dos literais.
3. Atualização segura = PUT parcial `?oslc.properties=` + `If-Match` (412 se obsoleto). PATCH = 501.
4. Transição só por `?_action=`. PUT em `rtc_cm:state` e ação inválida respondem 200 **sem efeito**.
5. Comentário: escrita só pela API OSLC CM 1.0; leitura pela 2.0.
6. Folha de horas: escrita só por API interna. Anexo: upload interno, link e download por OSLC.
7. RM: `instanceShape` é obrigatório. Escrita direta na stream funciona quando a política do projeto permite; changeset é política por projeto.

## Fatias

| Fatia | SPEC | Conteúdo | Esforço |
|---|---|---|---|
| F1 | `SPEC-ccm-correcoes` | Corrige D1, D2, D6, D9, D10, D11 (discovery de tipos, responsável, criação por tipo, transição, PUT parcial) | 🟡 |
| F2 | `SPEC-rm-correcoes` | Corrige D3, D7, D8; modo changeset opcional | 🟡 |
| F3 | `SPEC-ccm-colaboracao` | Comentários, links WI↔WI, re-parent, `set_field` genérico (enum por nome), filtros de consulta, ações por item | 🟡 |
| F4 | `SPEC-perfil-template` | Perfil por template de processo (valores padrão, rótulos de ações, enums dependentes) | 🟡 |
| F5 | `SPEC-ccm-interno` | Folha de horas e upload de anexo por API interna, atrás de flag | 🟡 |

Ordem: F1 → F2 → F3 → F4 → F5. F1 e F2 são correção de defeito e destravam o resto.

Fora de escopo: convenções de equipe (sinais de aprovação em comentário, regra de pontos, "iniciar tarefa" composta),
extração de texto de anexos, criação de plano de iteração (API interna, baixo valor), exclusão de work item (depende de permissão
administrativa; o servidor de referência negou).

## Decisões do mantenedor (abertas)
1. Perfil: em `~/.elm-mcp/profiles/` ou fornecido pelo cliente (ex.: skill do DAIA)?
2. API interna (F5): no pacote, atrás de `ELM_ENABLE_INTERNAL_API`, ou pacote separado?
3. Contato com o mantenedor do cliente de origem antes de reaproveitar lógica.

## Gates
Cada SPEC atômica: G0 (esta evidência) → G3 RED com respx (fixtures neutras) → G4 → G5 (autor ≠ verificador) →
**verificação viva** no servidor de referência com o script de aceitação da SPEC, em itens descartáveis.
