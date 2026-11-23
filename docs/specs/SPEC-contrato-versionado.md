# SPEC — Contrato versionado das tools

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §4 · **Onda:** W1
**Classe:** transversal. O elm-mcp é interface pública do DAIA; mudança de nome, parâmetro ou retorno precisa ser deliberada.

## Meta
Congelar o contrato (nomes das tools, parâmetros com tipo e obrigatoriedade, descrição) num snapshot versionado, com teste que roda sem ALM real.

## Critério de aceite (EARS)
- O sistema DEVE manter `tests/contract/tools.snapshot.json` com, por tool: nome, parâmetros (nome, tipo, obrigatório) e flag somente-leitura.
- QUANDO o conjunto real de tools divergir do snapshot, o teste de contrato DEVE falhar e indicar a diferença.
- QUANDO a divergência for intencional, o desenvolvedor DEVE regenerar o snapshot por comando documentado (`uv run python scripts/update_contract.py`) e registrar a mudança no CHANGELOG.
- O sistema DEVE tratar remoção ou renomeação de tool e parâmetro obrigatório novo como mudança incompatível: exige bump de versão menor (0.x) e linha em `### Changed`/`### Removed` do CHANGELOG.
- O teste NÃO DEVE depender de rede nem de credencial.

## Gates
G3 (o teste de contrato e a verificação de que alterar uma tool o quebra) → G4 (script de atualização + snapshot inicial) → G5.
