# SPEC — RM: conteúdo do requisito (XHTML idempotente, entrada Markdown, criação em 2 passos)

**Status:** DONE em G4/G5 (PR #73, draft; 2 loops de revisão + ajuste final verificado pelo gerente); aceitação viva de escrita feita em 24/nov/2026: criação em Markdown, atualização com `<div>` raiz já presente (continua um só) e exclusão confirmadas; o 403 em criação grande NÃO se reproduziu, então o 2º passo nunca foi exercitado ao vivo · **Release:** v0.7.0 · **Origem das ideias:** fluxos documentados do jandaia (`publicar_elm`) e a correção de div raiz do cadu-ia (rm-server), reescritos aqui; nenhum código copiado (ADR-006)

## Contexto
- O RM só aceita o corpo em `jazz_rm:primaryText` como literal XHTML bem formado, com um `<div xmlns="http://www.w3.org/1999/xhtml">` na raiz. O conteúdo entra sem escape dentro do RDF/XML.
- Hoje `create_requirement` e `update_requirement` sanitizam HTML (`sanitize_html_to_xhtml`) e embrulham num `<div>`. Se o conteúdo já traz um `<div xmlns=...>` na raiz, o resultado tem div duplo (a leitura de um requisito existente devolve esse formato).
- Markdown e texto puro não são aceitos pelo ALM: alguém precisa converter. Hoje o chamador faz isso.
- Relato de terceiros: o POST de criação com HTML grande ou complexo devolve 403; o PUT do mesmo conteúdo é aceito. **Não reproduzido** em 24/nov/2026 no projeto de referência: um POST de 8,4 KB de HTML devolveu 201 direto. O fallback em 2 passos só dispara após um 403 real e segue sem teste ao vivo.

## Critério de aceite (EARS)
- O sistema DEVE produzir exatamente UM `<div xmlns="http://www.w3.org/1999/xhtml">` na raiz, mesmo quando o conteúdo já vem com esse div (idempotência: aplicar duas vezes dá o mesmo resultado). Vale para `create_requirement` e `update_requirement`.
- QUANDO `content_format="markdown"`, o sistema DEVE converter Markdown para XHTML bem formado antes da sanitização (títulos, listas, tabelas, código, ênfase, links, imagens por URL), sem dependência nova obrigatória: conversor próprio mínimo OU dependência opcional (extra `markdown`); decidir no G0 por tamanho e risco, e documentar.
- `content_format` DEVE aceitar `html` (padrão, comportamento atual), `xhtml` (só valida e embrulha) e `markdown`; valor desconhecido é erro claro.
- O sistema DEVE rejeitar, antes de qualquer escrita, conteúdo que não vire XHTML bem formado (comportamento atual de `_assert_xhtml_wellformed` se mantém), e remover `script`, `style`, `iframe`, `object`, `embed` e emojis que quebram o XML.
- SE o POST de criação com conteúdo completo for recusado com 403 e o conteúdo for maior que um limite configurável (`ELM_RM_CREATE_FALLBACK_BYTES`, padrão 4096), ENTÃO o sistema PODE criar com conteúdo mínimo e aplicar o conteúdo completo por PUT com `If-Match`, confirmando por releitura. NÃO DEVE reenviar nenhum POST após timeout (regra de idempotência vigente); a criação em 2 passos só ocorre após recusa explícita (403), em que nada foi criado.
- SE o PUT do segundo passo falhar, ENTÃO o resultado DEVE dizer que o requisito FOI criado (com `uri`) e que o conteúdo completo não foi aplicado, sem apagar nada.

## Fora de escopo
Conversão de DOCX/PDF; upload de imagem por arquivo (só URL); módulos DOORS.

## Gates
G0: leitura viva do formato que o servidor devolve em `primaryText` (div duplo?) e confirmação do 403 em criação (a confirmação exige escrita em item descartável: ADIADA até a escrita ser liberada; o passo de 2 fases fica atrás do 403 real, então sem o 403 não dispara) → G3 (respx: div duplo, idempotência, Markdown por tipo de bloco, XHTML malformado recusado, 403 seguido de PUT, falha do PUT, timeout sem reenvio) → G4 → G5 → aceitação viva (leitura + escrita em item descartável).
