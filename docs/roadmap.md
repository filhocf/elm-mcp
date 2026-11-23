# elm-mcp — Roadmap

> O PLANO. Backlog de releases do elm-mcp (MCP server para IBM ELM via OSLC).
> Cadeia de rastreabilidade: **Roadmap (este doc) → RFC (`docs/rfc/`) → SPECs atômicas
> (`docs/specs/`) → PRs**. Decisões transversais em `docs/adr/`. Pesquisa de fundamento em
> `conhecimentos-de-ia/ferramentas/elm-mcp/`.
> Não duplica estado: o estado de cada spec vive no cabeçalho da própria spec (Status/gate).

## Visão

Elevar o elm-mcp de "cliente OSLC read + CRUD de work item" para o **middleware** entre o
processo agêntico (DAIA) e o ALM tradicional (IBM ELM), como projeto open-source de primeira linha
(tenant-neutro, instalável em um comando, gate agêntico no repo). Detalhe: `docs/rfc/RFC-001`.

## Legenda

Esforço 🟢 baixo · 🟡 médio · 🔴 alto. Status por spec no cabeçalho da spec (PLANNED/em gate/done).

## Releases

| Release | Tema | SPECs | Esf |
|---------|------|-------|-----|
| **v0.4.0** (não publicada — engloba toda a base profissional) | mcp 2.x + rename + profissionalização / repo-ouro | migração mcp2x (feita) · `SPEC-dead-code-cleanup` · `SPEC-docs-structure` (feita) · `SPEC-tenant-neutral` · `SPEC-repo-gold-ci` · `SPEC-readme-profissional` · ADR-004 (nome) | 🟢 |
| **v0.5.0** | RM write + discovery (membrana agêntica P0) | `SPEC-rm-create-requirement`, `SPEC-discovery-tools` | 🟡 |
| **v0.6.0** | Tudo o que estava represado (decisão do Claudio, 23/nov): correções F1/F2 + colaboração + QM + rastreabilidade + perfil + API interna + QM write | `SPEC-ccm-correcoes`, `SPEC-rm-correcoes`, `SPEC-ccm-colaboracao`, `SPEC-wi-custom-types`, `SPEC-qm-leitura`, `SPEC-oslc-links`, `SPEC-perfil-template`, `SPEC-ccm-interno`, `SPEC-qm-write` + transversais (contrato versionado, diagnóstico de gate 401, paginação/sessão, cache de shape, idempotência, agregação de horas) | 🔴 |
| **v0.7.0** | (absorvida pela v0.6.0) | — | — |
| **v0.6.0** | Rastreabilidade + tipos + colaboração CCM | `SPEC-oslc-links`, `SPEC-wi-custom-types`, `SPEC-ccm-colaboracao` (G4 GREEN) | 🟡 |
| **v0.7.0** | Teste + relatórios | `SPEC-qm-write`, `SPEC-reportbuilder-rs` | 🔴 |
| **v0.8.0** | Instalação first-class + credenciais | `SPEC-credential-keyring`, `SPEC-first-class-install` | 🟡 |
| **v1.0.0** | Quando o conjunto estiver sólido (decisão do Claudio) | — | — |

> **Renumeração (28/out):** a v0.4.0 não foi publicada no PyPI (só 0.1/0.2 estão lá), então ela
> absorve toda a base profissional (mcp2x + rename + higiene + repo-ouro + README) e só é publicada
> quando o repo estiver "primeira linha". As features sobem um degrau (RM write era v0.6, agora v0.5).

## Ordem de ataque

1. **Fechar a v0.4.0** antes de publicar: README profissional + dead-code + tenant-neutral no código
   + repo-gold-ci + ADR-004. Baixo risco, alto efeito de base. Piloto do checklist repo-ouro do DAIA.
2. **Publicar v0.4.0** no PyPI (tag `v0.4.0` → `publish.yml`) com o repo já profissional.
3. **v0.5.0** — `create_requirement` é o pedido nº 1 do DAIA (membrana); discovery habilita a camada
   de adaptação dinâmica por PA.
4. v0.6 → v0.8 na ordem da tabela; cada uma pode ser reordenada conforme necessidade do DAIA.

## Fora de escopo (limite da plataforma, não do roadmap)

- Criar iteração/sprint: a API OSLC CCM não permite (manual no EWM). O MCP só vincula (`plannedFor`).
- Acesso ao DB transacional do Jazz para relatórios: não suportado pela IBM (ver RFC-001 §relatórios).

## Fontes

- **F3**: levantamento interno + pesquisa de APIs em
  `conhecimentos-de-ia/ferramentas/elm-mcp/{levantamento-interno-alm,pesquisa-apis-elm}.md` (27/out/2026).
- **F3**: diagnóstico de código (codebase-memory) — 415 nós, `_find_creation_factory` sem chamador de produção.

## Plano da v0.6.0 (ondas)

Cadeia por SPEC: G0 (seven) → G3 RED (rok) → G4 GREEN (reg) → G5 (tuvok, máx. 2 loops) → verificação viva no servidor
de referência → PR **draft**. Um PR por SPEC. Ondas **sequenciais**: todas tocam `oslc.py` e `server.py`
(blast radius lateral), então paralelismo só entre trabalhos de arquivos disjuntos (ex.: testes RED da onda seguinte).
A v0.5.1 deixa de existir: F1 e F2 entram na v0.6.0.

| Onda | Conteúdo | Observação |
|---|---|---|
| W0 | F1 `ccm-correcoes` (branch `fix/f1-ccm-correcoes`) | D13: não escolher categoria automaticamente; sem `filed_against`, usar o default do servidor e enriquecer o `ToolError` com dica (`list_fields`). Default por PA vai ao perfil (W4). |
| W1 | F2 `rm-correcoes` (D3, D7, D8, changeset) + diagnóstico de gate (401/403/412 dizem QUAL gate barrou) + contrato versionado com testes de contrato | O diagnóstico e o contrato vêm primeiro porque todas as ondas seguintes herdam o formato de erro e o contrato. |
| W2 | F3 `ccm-colaboracao` + `wi-custom-types` + paginação >50 e refresh de sessão | Remove `TYPE_IDS` fixo. |
| W3 | F6 `qm-leitura` (D12) + `oslc-links` | Links destravam qm-write. |
| W4 | F4 `perfil-template` + cache TTL de `instanceShape` por PA + idempotência/duplicata | Resolve a decisão aberta 1 (local do perfil). |
| W5 | F5 `ccm-interno`: SÓ LEITURA (anexos) + `horas` (list_time, get_hours); escrita por API interna ADIADA (Gate -1) | Decisão aberta 2 fica para quando houver permissão de escrita e evidência de payload. |
| W6 | F7 `qm-write`: TestCase/TestPlan/TestSuite/TestScript (título), update, delete, vínculos TestCase→requisito/work item; TCER, TestResult e passos nativos ADIADOS (Gate -1) | Depende de W3 (leitura e links). Aceitação viva de escrita pendente. |

Fora da v0.6.0: F8/JRS (bloqueio de permissão REST no ELM; pedido de acesso ao admin), `credential-keyring` e
`first-class-install` (v0.9.0), convenções de equipe e exclusão de work item (ver `SPEC-ewm-delta`).

Cada onda fecha com: CHANGELOG, `docs/TOOLS.md`, cabeçalho Status da SPEC e `AGENTS.md` (contagem de tools) no mesmo PR.
Publicação (merge de release, tag `v*`, PyPI, GitHub Release) só como **draft** até OK do Claudio.
## Dívida conhecida

- Três leituras diretas via `client.get` não reautenticam em sessão expirada (as demais passam por `_request`): `server.py` (~139, detalhe de iteração), `oslc.py` (~374, iteração) e `oslc.py` (~604, estados do workflow). Migrar para `fetch`/`_request`.
- Em `_needs_reauth`, o teste de `j_security_check` no cabeçalho `Location` está sem cobertura real de redirecionamento de login (com `follow_redirects=True` o cliente raramente o vê); revisar se ainda é alcançável.
- O reenvio do POST/PUT num 403 sem `oslc:message` é deliberado (decisão do gerente); risco residual: uma recusa real é enviada duas vezes.
