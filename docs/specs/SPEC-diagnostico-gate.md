# SPEC — Diagnóstico de gate nos erros (401/403/404/412)

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Onda:** W1
**Classe:** transversal. Origem: dois incidentes reais em que o código estava correto e o gate de permissão barrou (401 de IAM em outro sistema; 401 do Report Builder por licença/papel). Regra do projeto: o MCP diz QUAL gate barrou.

## Meta
Todo erro HTTP de leitura ou escrita vira `ToolError` que nomeia o gate provável e a ação do usuário, em vez de `HTTPStatusError` genérico.

## Classificação
| Resposta | Gate nomeado |
|---|---|
| 401 no login (cabeçalho de falha de autenticação) | `credencial` — usuário/senha inválidos |
| 401 após sessão válida | `sessao` — sessão expirada (o cliente reautentica uma vez antes de reportar) |
| 403 com `oslc:message` | `regra-de-processo` — repassa a mensagem do servidor |
| 403 sem mensagem após reautenticar | `permissao` — papel/licença/visibilidade da project area insuficientes |
| 404 em recurso descoberto | `recurso-inexistente-ou-invisivel` — pode ser visibilidade da project area |
| 412 | `conflito` — recurso alterado; reler e repetir |
| 406 em QM | `formato` — QM só responde RDF/XML |

## Critério de aceite (EARS)
- O sistema DEVE expor uma função única de diagnóstico que recebe a resposta HTTP e o contexto (operação, URI) e devolve a mensagem com o gate nomeado.
- QUANDO uma tool de leitura ou escrita receber 401, 403, 404 ou 412, o sistema DEVE levantar `ToolError` cuja mensagem contenha o nome do gate da tabela e a ação sugerida.
- SE o servidor devolver `oslc:message`, ENTÃO a mensagem DEVE ser repassada sem alteração dentro do `ToolError`.
- O sistema NÃO DEVE incluir senha, cookie ou cabeçalho de autorização na mensagem.
- QUANDO o erro vier de recurso sem corpo reconhecível, o sistema DEVE ainda nomear o gate pelo status.

## Gates
G3 (respx por status e por tool de leitura/escrita) → G4 → G5.
