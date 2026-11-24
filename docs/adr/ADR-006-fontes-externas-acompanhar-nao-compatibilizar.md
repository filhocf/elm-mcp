# ADR-006 — Fontes externas: acompanhar a evolução, não compatibilizar

**Status:** ACEITA (decisão do Claudio, 24/nov/2026) · **Contexto:** análise de jandaia, cadu-ia e da contribuição da Susane Schmidt (nov/2026)

## Contexto
O jandaia (kit de desenvolvimento da Dataprev) instalava o `elm-alm-py==0.3.0` (nome antigo, fixo) e ficou duas versões publicadas
para trás: não acompanhou a evolução do MCP. O cadu-ia mantém servidores próprios de EWM e RM. Há ideias úteis nos dois, e
a análise de um deles ficou desatualizada em dias (o cadu-ia ganhou 16 commits entre a análise e a revisão seguinte).

## Decisão
1. **Acompanhar** as fontes de origem de forma sistemática: saber quando evoluíram e o que evoluiu, para skills, MCPs e
   ferramentas de linha de comando. Mecanismo: ferramenta `upstream-watch` (manifesto de fontes, relatório de mudanças,
   marca de "analisado até"). Especificação em `conhecimentos-de-ia/ferramentas/upstream-watch/SPEC.md`.
2. **Não compatibilizar.** O elm-mcp segue o próprio desenho (ADR-003: core genérico, perfil por instalação). Olhamos para
   jandaia e cadu-ia como eles olham para o DAIA: trocamos ideias e seguimos o nosso caminho. Não adotamos o formato de
   perfil, os nomes de tools nem o ritmo de release de nenhum deles.
3. **Reuso de código de terceiros** só com autorização do autor e licença clara (ver o caso da contribuição da Susane, em espera).
   Ideias, fluxos documentados e evidência de comportamento do servidor podem ser usados, reescritos.

## Consequências
- Cada ideia aproveitada vira SPEC (funcionalidade) ou issue (bug) nossa, com G0 e testes próprios.
- Sem aviso proativo aos consumidores sobre o nome `elm-mcp` (o jandaia ainda usa `elm-alm-py`); só se eles perguntarem.
- O custo é manter o manifesto de fontes atualizado e ler o relatório periodicamente.
