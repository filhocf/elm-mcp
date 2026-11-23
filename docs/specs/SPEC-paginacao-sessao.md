# SPEC — Paginação transparente e sessão em consultas longas

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Onda:** W2
**Classe:** transversal. Origem: lacunas de uso real (SPEC-005 do DAIA): limite de 50 itens por consulta e sessão que expira no meio de um lote.

## Meta
Consultas OSLC devolvem o que o chamador pediu (até um limite explícito), seguindo a paginação do servidor, e sobrevivem à expiração de sessão no meio do lote sem duplicar nem perder itens.

## Critério de aceite (EARS)
- QUANDO `query_resources` (e as tools que a usam) receber `limit`, o sistema DEVE seguir `oslc:nextPage` (ou `rdfs:member` + `oslc:ResponseInfo/nextPage`) até reunir `limit` itens ou esgotar as páginas.
- QUANDO `limit` não for informado, o sistema DEVE aplicar um limite padrão de 50 itens (comportamento atual) e DEVE sinalizar `truncated: true` se existir próxima página.
- O sistema NÃO DEVE retornar mais de `limit` itens nem o mesmo item duas vezes.
- QUANDO a sessão expirar durante a paginação (401 ou redirecionamento ao login), o sistema DEVE reautenticar uma vez e repetir só a página que falhou.
- SE a reautenticação falhar ou a página falhar de novo, ENTÃO o sistema DEVE levantar `ToolError` com o gate nomeado (SPEC-diagnostico-gate) e informar quantos itens já haviam sido coletados.
- O sistema DEVE aplicar um teto absoluto de 1000 itens por chamada (proteção contra consulta ilimitada).

## Gates
G3 (respx: 3 páginas, limite no meio da página, expiração na página 2, nextPage ausente, duplicata entre páginas, teto) → G4 → G5.
