# SPEC — Cache de descoberta por project area (TTL)

**Status:** IMPLEMENTADA (G4, aguarda G5) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §4 · **Onda:** W4
**Classe:** transversal. Origem: backlog "descobrir instanceShape por PA com cache TTL"; evidência de custo: `create_workitem` e os filtros de `list_workitems` repetem rootservices → catálogo → services → shapes a cada chamada.

## Meta
Reduzir as requisições de descoberta repetidas (provider, factories, shapes, allowedValues, workflow) por meio de um cache em memória, com TTL, por processo e por project area, sem mudar o contrato das tools.

## Critério de aceite (EARS)
- O sistema DEVE manter um cache em memória, chaveado por (servidor, domínio, project area, recurso), com TTL padrão de 300 s configurável por `ELM_CACHE_TTL` (0 desliga).
- QUANDO a mesma descoberta for pedida dentro do TTL, o sistema NÃO DEVE repetir a requisição ao servidor.
- QUANDO uma escrita falhar com erro que indique shape ou valor obsoleto (400/409 de validação), o sistema DEVE invalidar a entrada da project area e repetir a descoberta uma única vez antes de reportar o erro.
- O sistema DEVE oferecer `refresh=True` nas tools de descoberta (`list_types`, `list_fields`, `list_workflow`) para ignorar e repovoar o cache.
- O cache NÃO DEVE ser compartilhado entre servidores distintos (`ELM_URL`) nem guardar credenciais ou cookies.
- QUANDO o perfil (SPEC-perfil-template) existir, o sistema DEVE usá-lo como fallback se a descoberta falhar.

## Gates
G3 (respx: segunda chamada sem requisição; TTL expirado repete; invalidação por erro; `refresh`; isolamento por servidor) → G4 → G5.
