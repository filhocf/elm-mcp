# ADR-005 — Perfil de adaptação: arquivo JSON local, fora do pacote

**Status:** ACEITA (decisão do gerente, 23/nov/2026; Claudio pode revisar) · **Contexto:** `SPEC-perfil-template`, decisão aberta 1 de `SPEC-ewm-delta`

## Decisão
O perfil é um **arquivo JSON** lido de um diretório externo ao pacote:
1. `ELM_MCP_PROFILE_DIR` (variável de ambiente), se definida;
2. senão `~/.elm-mcp/profiles/`.

Estrutura: `templates/<nome>.json` (valores por template de processo) e `project-areas/<chave>.json` (sobrescrita por project area, com `"template": "<nome>"`).
O cliente (ex.: skill do DAIA) pode gerar os arquivos e apontar `ELM_MCP_PROFILE_DIR`; o pacote não embute nenhum valor de instalação.

## Alternativas
- **YAML:** mais legível, mas exige dependência nova (PyYAML) num pacote hoje com poucas dependências. Rejeitada.
- **Perfil passado pelo cliente a cada chamada:** acopla cada tool ao cliente e infla o contrato. Rejeitada; o diretório já permite o cliente fornecer o arquivo.
- **Perfil no pacote:** viola `ADR-003` (core genérico, sem valores de organização). Rejeitada.

## Consequências
- Zero dependência nova; JSON é validável por schema e fácil de gerar a partir do discovery.
- Sem perfil, o sistema opera só com discovery (comportamento atual).
- Um comando `elm-mcp profile draft` gera rascunho a partir do discovery para revisão humana.
