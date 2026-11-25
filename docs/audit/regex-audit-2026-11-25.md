# Auditoria de regex (ReDoS), 2026-11-25

Issue: filhocf/elm-mcp#88. Base: `main` 798394d, branch `fix/88-regex-audit`. A medição ('antes') foi feita sobre `main` 798394d; o conserto dos 30 pontos LENTOS (seção "Conserto") está no mesmo branch e a coluna 'depois' traz os tempos medidos depois dele.

## Método

- Inventário: `grep` de `re.compile|search|match|findall|finditer|sub|subn|fullmatch|split` e `_re.` em `src/elm_mcp/*.py` (92 ocorrências em 6 arquivos; `oslc.py` usa `import re as _re`, e `list_service_providers` tem `import re` local).
- Medição: script avulso (não commitado) chama a FUNÇÃO REAL que contém o padrão, com o I/O de rede substituído por fakes, e cronometra com `time.perf_counter`. Máquina local, Python 3.11, sem coverage. Tamanho = nº de bytes da entrada (unidade repetida × N). Teto de 60 s por medição (`>60 s` = abortado). Para 200 KB > 20 s não se mediu 1 MB.
- Padrões de constantes de módulo (URLs, ids, números) foram medidos com 20 unidades repetidas × 3 caudas (`''`, `'!'`, `'\n!'`) por padrão; o valor da tabela é o tempo total das 60 combinações, não de uma.
- Veredito LENTO: > 0,5 s a 200 KB, ou razão 200 KB/100 KB > 3 (quadrático) com mais de 0,1 s. Todos os LENTO aqui são quadráticos (razão ≈ 4 ao dobrar o tamanho; ≈ 25 de 200 KB para 1 MB).
- Causa comum: `[^>]*`/`.*?` sem limite, reexecutado a partir de CADA abertura sem `>` / sem fechamento. Cada abertura reescaneia até o fim do texto.

## Tabela de medições (pior caso por padrão)

| arquivo:linha | padrão (resumido) | fonte da entrada | entrada patológica | 200 KB | 1 MB | veredito | depois (200 KB / 1 MB) | teste de tempo |
|---|---|---|---|---|---|---|---|---|
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename*=' × N | 16.8 s | >60 s | **LENTO** | 0.014 s / 0.077 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[attachments.py:30 filename*]` |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename*=x'' × N | 0.009 s | 0.047 s | — | **OK** |  |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename*=a'b' × N | 0.009 s | 0.046 s | — | **OK** |  |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename="' × N | 0.002 s | 0.008 s | — | **OK** |  |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename=' × N | 0.011 s | 0.058 s | — | **OK** |  |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename*                    ' × N | 0.003 s | 0.014 s | — | **OK** |  |
| attachments.py:30-31/126/136 | `filename\*\s*=\s*([^';]*)'[^';]*'([^;]*) ; filename\s*=\s*(\"..\"\|[^;\s]*)` | servidor (cabeçalho Content-Disposition) | 'filename                    ' × N | 0.003 s | 0.013 s | — | **OK** |  |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''['' × N | 0.105 s | 0.517 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''[a]('' × N | 0.150 s | 0.730 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''!['' × N | 0.101 s | 0.498 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''[a](b('' × N | 0.111 s | 0.539 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''**'' × N | 0.046 s | 0.210 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''*a '' × N | 0.113 s | 0.624 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''`'' × N | 0.019 s | 0.088 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''- '' × N | 0.026 s | 0.135 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''- - '' × N | 0.026 s | 0.145 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''#'' × N | 0.080 s | 0.395 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''# a\n'' × N | 0.157 s | 0.769 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''\| a \|\n'' × N | 0.140 s | 0.656 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''1. a\n'' × N | 0.134 s | 0.822 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | '' '' × N | 0.019 s | 0.095 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''```\n'' × N | 0.094 s | 0.545 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''---\n'' × N | 0.100 s | 0.508 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''[a]((((((((((((((((('' × N | 0.077 s | 0.393 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| markdown_xhtml.py:20-27 | `_SAFE_URL _HEADING _FENCE _HR _LIST_ITEM _TABLE_SEP_CELL _URL` | usuário (conteúdo markdown) | ''[a](http://x/((((((('' × N | 0.079 s | 0.403 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:61/76 | `<\w{0,32}:?message\b[^>]{0,2000}>([^<]+)</\w{0,32}:?message>` | servidor (corpo de erro XML) | '<message ' × N | 0.081 s | 0.393 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| oslc.py:61/76 | `<\w{0,32}:?message\b[^>]{0,2000}>([^<]+)</\w{0,32}:?message>` | servidor (corpo de erro XML) | '<a:message>x' × N sem fechamento | 0.003 s | 0.017 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| oslc.py:61/76 | `<\w{0,32}:?message\b[^>]{0,2000}>([^<]+)</\w{0,32}:?message>` | servidor (corpo de erro XML) | '<a:message>' × N | 0.003 s | 0.017 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| oslc.py:61/76 | `<\w{0,32}:?message\b[^>]{0,2000}>([^<]+)</\w{0,32}:?message>` | servidor (corpo de erro XML) | '<message ' + 300 atributos, × N | 0.001 s | 0.004 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| oslc.py:61/76 | `<\w{0,32}:?message\b[^>]{0,2000}>([^<]+)</\w{0,32}:?message>` | servidor (corpo de erro XML) | '<message>' + 'a' × N sem fechar | 0.003 s | 0.014 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| oslc.py:264 | `<\w+:ServiceProvider>\s*<title>..</title>\s*.*?<\w+:services\s+[^>]*rdf:resource=\"..\" (DOTALL)` | servidor (catálogo RM) | '<a:ServiceProvider><title>x</title>' × N, sem services | 4.206 s | >60 s | **LENTO** | 0.001 s / 0.002 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:264 list_service_providers]` |
| oslc.py:445 | `<oslc_config:component\s[^>]*rdf:resource=".."` | servidor (service provider / component / configurations) | '<oslc_config:component ' × N, sem '>' | 0.587 s | 13.0 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:445 rm component]` |
| oslc.py:451 | `<(\w+:)?configurations\s[^>]*rdf:resource=` | servidor (service provider / component / configurations) | '<oslc:configurations ' × N, sem '>' | 0.810 s | 19.2 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:451 rm configurations]` |
| oslc.py:454 | `<(\w+:)?member\s[^>]*rdf:resource=` | servidor (service provider / component / configurations) | '<rdfs:member ' × N, sem '>' | 2.147 s | 54.5 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:454 rm member]` |
| oslc.py:834 | `filed\s*against\|categor (IGNORECASE)` | servidor (oslc:message) | 'filed' + espaços × N | 0.001 s | 0.007 s | — | **OK** |  |
| oslc.py:834 | `filed\s*against\|categor (IGNORECASE)` | servidor (oslc:message) | 'filed ' × N | 0.005 s | 0.030 s | — | **OK** |  |
| oslc.py:840/857 | `_NEEDS_TO_BE_SET (limitado a 2000 chars)` | servidor (oslc:message) | 'The  ' × N | 0.000 s | 0.000 s | **OK** | — | já existe: `tests/test_hint_and_add_child.py::test_required_attribute_hint_is_fast_on_hostile_messages` |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<img ' × N | 0.043 s | 0.206 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<input ' × N | 0.042 s | 0.191 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<meta ' × N | 0.038 s | 0.203 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<html ' × N | 0.039 s | 0.197 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<body ' × N | 0.040 s | 0.196 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<head ' × N | 0.040 s | 0.197 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<script ' × N | 0.000 s | 0.000 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<script>' × N | 0.000 s | 0.001 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<embed> ' × N | 0.018 s | 0.090 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '&' × N | 0.165 s | 0.861 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<br ' × N | 0.047 s | 0.241 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<hr ' × N | 0.048 s | 0.241 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<br                 ' × N | 0.037 s | 0.182 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '</script ' × N | 0.037 s | 0.189 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<div xmlns="http://w' × N | 0.019 s | 0.171 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '<div ' × N | 0.041 s | 0.202 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1175-1314 | `scanners + _re.sub (<hr\s*/?>, <br\s*/?>, & lookahead, emoji, _OPEN_*, _CLOSE_*, _ROOT_OPEN, _DIV_*)` | usuário (conteúdo html/xhtml) | '😀' × N | 0.014 s | 0.072 s | **OK** | — | já existe: `tests/test_rm_conteudo_loop2.py` (200 KB, limite 5 s) |
| oslc.py:1433 | `<\w*:?CreationFactory\b.*?</\w*:?CreationFactory> (DOTALL)` | servidor (service provider RM) | '<oslc:CreationFactory>' × N sem fechamento | 5.444 s | >60 s | **LENTO** | 0.001 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1433 CreationFactory unclosed]` |
| oslc.py:1434 | `<\w*:?resourceType\s+[^>]*rdf:resource=\"..\"` | servidor (bloco CreationFactory) | bloco fechado com '<a:resourceType ' × N | 1.974 s | 48.5 s | **LENTO** | 0.001 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1434 resourceType]` |
| oslc.py:1437 | `<\w*:?creation\s+[^>]*rdf:resource=\"..\"` | servidor (bloco CreationFactory) | bloco com resourceType válido + '<a:creation ' × N | 1.899 s | 50.2 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1437 creation]` |
| oslc.py:1494 | `<\w*:?identifier[^>]*>([^<]+)</\w*:?identifier>` | servidor (corpo do PUT/POST) | '<a:identifier ' × N | 0.951 s | 26.3 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1494 identifier]` |
| oslc.py:1494 | `<\w*:?identifier[^>]*>([^<]+)</\w*:?identifier>` | servidor (corpo do PUT/POST) | '<identifier>x' × N sem fechar | 0.003 s | 0.015 s | **OK** | — |  |
| oslc.py:1807 | `<jazz_rm:primaryText[^>]*>` | servidor (GET do requisito) | '<jazz_rm:primaryText ' × N | 0.589 s | 15.1 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1807 primaryText]` |
| oslc.py:1846-1852 | `(<\w*:?Description\b[^>]*rdf:about=\"URI\"[^>]*>)(.*?)(</\w*:?Description>) (DOTALL) + fallback` | servidor (GET do requisito) | '<rdf:Description ' × N sem '>' | 2.500 s | 60.0 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1846 Description (about)]` |
| oslc.py:1846 | `(<\w*:?Description\b[^>]*rdf:about=\"URI\"[^>]*>)... ` | servidor (GET do requisito) | '<rdf:Description rdf:about="outra" ' × N | 0.970 s | 22.6 s | **LENTO** | 0.000 s / 0.000 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1852 Description (fallback)]` |
| oslc.py:1860 | `(<\w*:?title[^>]*>)(.*?)(</\w*:?title>) (DOTALL, count=1)` | servidor (GET do requisito) | Description válido + '<dcterms:title ' × N | 0.831 s | 20.6 s | **LENTO** | 0.002 s / 0.008 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1860 title]` |
| oslc.py:1939 | `<\w*:?property\b.*?</\w*:?property> (DOTALL, sub)` | servidor (resourceShape) | '<oslc:property>' × N sem fechamento | 8.640 s | >60 s | **LENTO** | 0.001 s / 0.006 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1939 property]` |
| oslc.py:1940 | `<\w*:?title[^>]*>([^<]+)</\w*:?title>` | servidor (resourceShape) | '<dcterms:title ' × N | 0.893 s | 22.9 s | **LENTO** | 0.001 s / 0.008 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:1940 title]` |
| oslc.py:2006 | `CreationFactory .*? (DOTALL)` | servidor (service provider CCM) | '<oslc:CreationFactory>' × N sem fechamento | 6.150 s | >60 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2006 ccm CreationFactory unclosed]` |
| oslc.py:2007 | `<\w*:?resourceType\s+[^>]*rdf:resource=\"..\"` | servidor (service provider CCM) | bloco com '<a:resourceType ' × N | 1.598 s | 42.9 s | **LENTO** | 0.001 s / 0.004 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2007 ccm resourceType]` |
| oslc.py:2008 | `<\w*:?creation\s+[^>]*rdf:resource=\"..\"` | servidor (service provider CCM) | bloco cm#ChangeRequest + '<a:creation ' × N | 1.896 s | 49.4 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2008 ccm creation]` |
| oslc.py:2014 | `<\w*:?resourceShape\s+[^>]*rdf:resource=\"..\"` | servidor (service provider CCM) | bloco válido + '<a:resourceShape ' × N | 1.554 s | 39.3 s | **LENTO** | 0.001 s / 0.003 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2014 ccm resourceShape]` |
| oslc.py:2015 | `<\w*:?title[^>]*>([^<]*)</\w*:?title>` | servidor (service provider CCM) | bloco válido + '<a:title ' × N | 1.493 s | 38.0 s | **LENTO** | 0.001 s / 0.004 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2015 ccm title]` |
| oslc.py:2017+2011 | `_FACTORY_TITLE_RE ^Location for creation of (.+?) change requests$ / title longo` | servidor (service provider CCM) | bloco válido, título de N 'a' | 0.001 s | 0.007 s | **OK** | — |  |
| oslc.py:2049 | `CreationFactory .*? (DOTALL)` | servidor (service provider) | '<oslc:CreationFactory>' × N sem fechamento | 5.995 s | >60 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2049 shapes CreationFactory unclosed]` |
| oslc.py:2050 | `<\w*:?resourceType\s+[^>]*rdf:resource` | servidor (service provider) | bloco com '<a:resourceType ' × N | 1.616 s | 41.0 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2050 shapes resourceType]` |
| oslc.py:2053 | `<\w*:?resourceShape\s+[^>]*rdf:resource=\"..\" (findall)` | servidor (service provider) | bloco rm#Requirement + '<a:resourceShape ' × N | 1.397 s | 36.6 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2053 shapes resourceShape]` |
| oslc.py:2220 | `<\w*:?Description\b[^>]*>.*?</\w*:?Description> (DOTALL)` | servidor (allowedValues) | '<rdf:Description ' × N sem '>' | 0.744 s | 18.4 s | **LENTO** | 0.001 s / 0.003 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2220 allowed Description no '>']` |
| oslc.py:2220 | `<\w*:?Description\b[^>]*>.*?</\w*:?Description> (DOTALL)` | servidor (allowedValues) | '<rdf:Description>' × N sem fechamento | 8.291 s | >60 s | **LENTO** | 0.001 s / 0.004 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2220 allowed Description unclosed]` |
| oslc.py:2221 | `rdf:about=\"([^\"]+)\"` | servidor (allowedValues) | Description com about de N chars | 0.000 s | 0.002 s | **OK** | — |  |
| oslc.py:2222 | `<\w*:?title[^>]*>([^<]+)</\w*:?title>` | servidor (allowedValues) | Description fechado + '<dcterms:title ' × N | 0.895 s | 20.9 s | **LENTO** | 0.002 s / 0.008 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2222 allowed title]` |
| oslc.py:2227 | `<\w*:?allowedValue\s+[^>]*rdf:resource=\"..\"` | servidor (allowedValues) | '<oslc:allowedValue ' × N | 0.648 s | 18.6 s | **LENTO** | 0.001 s / 0.004 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2227 allowedValue]` |
| oslc.py:2281 | `/shapes/workitems/(.+)$` | servidor/usuário (instanceShape URI) | '/shapes/workitems/x\n' × N | 0.000 s | 0.002 s | **OK** | — |  |
| oslc.py:2281 | `/shapes/workitems/(.+)$` | servidor/usuário (instanceShape URI) | '/shapes/workitems/' × N | 0.031 s | 0.154 s | **OK** | — |  |
| oslc.py:2285 | `(/oslc/(?:contexts\|context)/[^/]+/)shapes/workitems/.*$` | servidor/usuário (instanceShape URI) | '/oslc/contexts/a/shapes/workitems/x\n' × N | 0.001 s | 0.005 s | — | **OK** |  |
| oslc.py:2341 | `<\w*:?details\s+[^>]*rdf:resource=\"..\"` | servidor (service provider RM) | '<a:details ' × N | 1.218 s | 31.6 s | **LENTO** | 0.000 s / 0.001 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2341 details]` |
| oslc.py:2602-2603 | `<br\s*/?>\|</(?:div\|p)>  (+ <[^>]+> da linha 2603)` | servidor (texto do comentário) | '<br ' × N e '<br'+espaços | 10.9 s | >60 s | **LENTO** | 0.006 s / 0.028 s | coberto por `[oslc.py:2603 plain '<a ']` (mesmo `<[^>]+>`) |
| oslc.py:2603 | `<[^>]+>` | servidor (texto do comentário) | '<' × N | 14.2 s | >60 s | **LENTO** | 0.003 s / 0.013 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2603 plain '<']` |
| oslc.py:2603 | `<[^>]+>` | servidor (texto do comentário) | '<a ' × N | 4.960 s | >60 s | **LENTO** | 0.006 s / 0.028 s | `tests/test_regex_timing.py::test_pathological_input_is_handled_in_linear_time[oslc.py:2603 plain '<a ']` |
| qm_write.py:52/184 | `^\s*<\?xml[^>]*encoding\s*=\s*[\"']\s*(?:utf-16\|utf-32\|ucs-[24]) (IGNORECASE, bytes)` | usuário (XML de entrada) | '<?xml' + ' encoding=' × N | 0.002 s | 0.008 s | **OK** | — |  |
| qm_write.py:52/184 | `idem` | usuário (XML de entrada) | '<?xml' + 'encoding=" ' × N | 0.002 s | 0.012 s | **OK** | — |  |
| qm_write.py:52/184 | `idem` | usuário (XML de entrada) | '<?xml' + espaços × N | 0.003 s | 0.015 s | **OK** | — |  |
| qm_write.py:336 | `<([^\s/>]+)` | usuário (XML de entrada) | '<' + 'a' × N | 0.001 s | 0.003 s | **OK** | — |  |

### Constantes de módulo (fuzz com 60 combinações)

| arquivo:linha | padrão | uso | 200 KB (total das 60) | 1 MB (total das 60) | veredito |
|---|---|---|---|---|---|
| oslc.py:383 | `_QUERY_RESULTS_RE (search)` | constante-fuzz | 0.004 s | 0.024 s | **OK** |
| oslc.py:1011 | `_STATE_URI_RE (search)` | constante-fuzz | 0.006 s | 0.029 s | **OK** |
| oslc.py:1016 | `split (split)` | constante-fuzz | 0.122 s | 0.624 s | **OK** |
| oslc.py:1312 | `_ROOT_OPEN (fullmatch)` | constante-fuzz | 0.000 s | 0.003 s | **OK** |
| oslc.py:1992 | `_FACTORY_TITLE_RE (match)` | constante-fuzz | 0.005 s | 0.028 s | **OK** |
| oslc.py:2139 | `_PLAIN_SEGMENT_RE (fullmatch)` | constante-fuzz | 0.014 s | 0.067 s | **OK** |
| oslc.py:2379 | `_TEAM_URL_PATH_RE (match)` | constante-fuzz | 0.000 s | 0.003 s | **OK** |
| oslc.py:2380 | `_SAFE_ID_RE (fullmatch)` | constante-fuzz | 0.013 s | 0.067 s | **OK** |
| oslc.py:2490 | `_UNASSIGNED_RE (match)` | constante-fuzz | 0.023 s | 0.115 s | **OK** |
| oslc.py:2491 | `_WORKFLOW_STATE_RE (search)` | constante-fuzz | 0.006 s | 0.029 s | **OK** |
| oslc.py:2492 | `_WORKITEM_ID_RE (search)` | constante-fuzz | 0.013 s | 0.069 s | **OK** |
| oslc.py:2837 | `_PROPERTY_PREFIX_RE (fullmatch)` | constante-fuzz | 0.005 s | 0.025 s | **OK** |
| oslc.py:2906 | `_FIELD_QNAME_RE (match)` | constante-fuzz | 0.003 s | 0.017 s | **OK** |
| oslc.py:2907 | `_FIELD_NAME_RE (match)` | constante-fuzz | 0.006 s | 0.030 s | **OK** |
| oslc.py:2924 | `_INTEGER_RE (fullmatch)` | constante-fuzz | 0.002 s | 0.013 s | **OK** |
| oslc.py:2925 | `_DECIMAL_RE (fullmatch)` | constante-fuzz | 0.021 s | 0.107 s | **OK** |
| oslc.py:3049 | `_ID_RE (fullmatch)` | constante-fuzz | 0.015 s | 0.079 s | **OK** |
| oslc.py:3050 | `_STATE_ID_RE (fullmatch)` | constante-fuzz | 0.089 s | 0.540 s | **OK** |
| profile.py:25 | `_SAFE_NAME (match)` | constante-fuzz | 0.009 s | 0.044 s | **OK** |
| qm.py:51 | `_INT_RE (fullmatch)` | constante-fuzz | 0.003 s | 0.014 s | **OK** |
| qm_write.py:47 | `_QM_RESOURCE_PATH (fullmatch)` | constante-fuzz | 0.000 s | 0.003 s | **OK** |
| qm_write.py:48 | `_SAFE_PATH (fullmatch)` | constante-fuzz | 0.000 s | 0.003 s | **OK** |

### Testes de tempo que já existiam no repo

| padrão | teste |
|---|---|
| `_MESSAGE_XML` (oslc.py:61) | `tests/test_hint_and_add_child.py::test_server_message_is_fast_on_hostile_xml` |
| `_NEEDS_TO_BE_SET` (oslc.py:840) | `tests/test_hint_and_add_child.py::test_required_attribute_hint_is_fast_on_hostile_messages` (+ `..._finds_a_legit_message_after_garbage...`) |
| Markdown (markdown_xhtml.py:20-27) | `tests/test_rm_conteudo_loop2.py::test_h_markdown_is_linear` |
| Sanitizador HTML (oslc.py:1175-1302) | `tests/test_rm_conteudo_loop2.py::test_h_html_sanitizer_is_linear` |
| `_unwrap_root_div` (oslc.py:1312-1314) | `tests/test_rm_conteudo_loop2.py::test_h_xhtml_unwrap_is_linear`, `::test_deeply_nested_root_divs_unwrap_in_linear_time` |

`tests/test_list_teams.py`, `tests/test_enum_titles.py` e `tests/test_w4_cache.py` cronometram volume (1000 times, 5000 literais) e cache, não backtracking.

## Testes novos (`tests/test_regex_timing.py`)

- `test_pathological_input_is_handled_in_linear_time[caso]`: 30 casos, um por ponto de chamada LENTO (os padrões idênticos em linhas diferentes têm caso próprio porque o conserto pode não ser compartilhado). Chama a função real com o I/O trocado por fake, entrada de 200 KB ou maior, limite de 5 s (o CI com coverage é ~10x mais lento).
- `test_legitimate_input_keeps_its_behavior[caso]`: 14 casos funcionais (entrada legítima pequena, resultado exato). Passam hoje e devem continuar verdes depois do conserto.
- Tamanho da entrada: onde 200 KB já passa de 5 s (1939, 2006, 2049, 2220 sem fechamento, filename*, `<` repetido) ficou 200 KB. Nos demais subiu de 230 KB a 660 KB, até o código atual levar mais de 6 s numa execução sem coverage.
- Fora de escopo: `oslc.py:2602` (`<br\s*/?>|</(?:div|p)>`) mede OK (0,002 s); o caso `[oslc.py:2603 plain '<a ']` cobre o `<[^>]+>` seguinte.

### Resultado RED medido (`uv run pytest -q tests/test_regex_timing.py`, sem coverage)

`30 failed, 14 passed in 225.92s`. Os 30 falham todos em `assert elapsed < LIMIT` (nenhum por exceção ou resultado errado):

```
attachments.py:30 filename*: 14.5s for 200000 bytes (limit 5.0s)
oslc.py:1433 CreationFactory unclosed: 7.4s for 220000 bytes (limit 5.0s)
oslc.py:1434 resourceType: 6.6s for 400000 bytes (limit 5.0s)
oslc.py:1437 creation: 6.4s for 370000 bytes (limit 5.0s)
oslc.py:1494 identifier: 6.2s for 520000 bytes (limit 5.0s)
oslc.py:1807 primaryText: 6.7s for 660000 bytes (limit 5.0s)
oslc.py:1846 Description (about): 6.4s for 320000 bytes (limit 5.0s)
oslc.py:1852 Description (fallback): 6.8s for 520000 bytes (limit 5.0s)
oslc.py:1860 title: 6.7s for 560000 bytes (limit 5.0s)
oslc.py:1939 property: 9.2s for 200000 bytes (limit 5.0s)
oslc.py:1940 title: 6.2s for 540000 bytes (limit 5.0s)
oslc.py:2006 ccm CreationFactory unclosed: 6.1s for 200000 bytes (limit 5.0s)
oslc.py:2007 ccm resourceType: 6.6s for 400000 bytes (limit 5.0s)
oslc.py:2008 ccm creation: 6.3s for 370000 bytes (limit 5.0s)
oslc.py:2014 ccm resourceShape: 6.4s for 410000 bytes (limit 5.0s)
oslc.py:2015 ccm title: 6.3s for 420000 bytes (limit 5.0s)
oslc.py:2049 shapes CreationFactory unclosed: 6.0s for 200000 bytes (limit 5.0s)
oslc.py:2050 shapes resourceType: 6.6s for 400000 bytes (limit 5.0s)
oslc.py:2053 shapes resourceShape: 6.9s for 430000 bytes (limit 5.0s)
oslc.py:2220 allowed Description no '>': 6.5s for 590000 bytes (limit 5.0s)
oslc.py:2220 allowed Description unclosed: 8.1s for 200000 bytes (limit 5.0s)
oslc.py:2222 allowed title: 6.2s for 540000 bytes (limit 5.0s)
oslc.py:2227 allowedValue: 6.7s for 630000 bytes (limit 5.0s)
oslc.py:2341 details: 6.6s for 470000 bytes (limit 5.0s)
oslc.py:2603 plain '<': 14.3s for 200000 bytes (limit 5.0s)
oslc.py:2603 plain '<a ': 8.1s for 250000 bytes (limit 5.0s)
oslc.py:264 list_service_providers: 9.7s for 250000 bytes (limit 5.0s)
oslc.py:445 rm component: 7.8s for 660000 bytes (limit 5.0s)
oslc.py:451 rm configurations: 8.2s for 570000 bytes (limit 5.0s)
oslc.py:454 rm member: 9.1s for 350000 bytes (limit 5.0s)
```

## Achados

1. 30 pontos de chamada são quadráticos. Todos têm a forma `<tag\s+[^>]*...` / `<tag[^>]*>` / `<tag\b.*?</tag>` aplicada a texto vindo do servidor sem limite de tamanho.
2. O pior caso é uma resposta de 200 KB com aberturas repetidas sem `>` ou sem fechamento (8 a 17 s a 200 KB; mais de 60 s a 1 MB). Uma resposta assim bloqueia o event loop do MCP durante a execução do regex. Não verifiquei se algum limite de tamanho de corpo existe no caminho de rede; as medições chamam as funções com o texto já em memória.
3. `attachments.py:30` (`filename*=` repetido) é o pior por byte (16,8 s a 200 KB), mas a entrada é um cabeçalho HTTP, provavelmente limitado pelo cliente HTTP (não verificado); por extrapolação quadrática, 50 KB levariam cerca de 1 s. Risco menor na prática, mesmo conserto.
4. `oslc.py:2603` `<[^>]+>` (`_plain`): texto de comentário do servidor com `<` sem `>` repetido. 14 s a 200 KB.
5. Padrões já corrigidos nas revisões anteriores (`_MESSAGE_XML`, `_NEEDS_TO_BE_SET`, scanners do sanitizador, Markdown) medem OK.
6. As funções de entrada de usuário (`prepare_content` markdown/html) medem OK: máximo 0,86 s a 1 MB.

## Limitações

- Medido em uma máquina, Python 3.11, uma execução por ponto (sem repetição); a razão 100 KB/200 KB/1 MB é o que sustenta o veredito quadrático, não o valor absoluto.
- Constantes de módulo (ids, URLs, números) foram avaliadas com fuzz de 20 unidades x 3 caudas, não por análise formal; nenhuma passou de 0,15 s a 200 KB.
- O inventário usa `grep`; chamadas indiretas a regex (por exemplo `fnmatch`, `str.split`) não são regex e ficaram fora.
- Entradas de 1 MB só foram medidas quando 200 KB levou menos de 20 s.

## Inventário completo por linha (92 ocorrências)

Cada linha de `src/` com regex, o que a cobre na medição e o pior caso. `san:*`/`md:*` = família de entradas do sanitizador HTML / conversor Markdown (16 e 18 entradas).

| arquivo:linha | medição que a cobre | pior 200 KB | pior 1 MB | veredito |
|---|---|---|---|---|
| attachments.py:30 | att:filename*=, att:filename*=x', att:filename*=a'b (+1) | 16.8 s | >60 s | **LENTO** |
| attachments.py:31 | att:filename=", att:filename=, att:filename+sp | 0.011 s | 0.058 s | **OK** |
| markdown_xhtml.py:20 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:21 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:22 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:23 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:24 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:25 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| markdown_xhtml.py:27 | md:[, md:[a](, md:![ (+15) | 0.157 s | 0.822 s | **OK** |
| oslc.py:61 | msg61, msg61c | 0.081 s | 0.393 s | **OK** |
| oslc.py:264 | sp264 | 4.206 s | >60 s | **LENTO** |
| oslc.py:383 | const:oslc.py:383 _QUERY_RESULTS_RE | 0.004 s | 0.024 s | **OK** |
| oslc.py:445 | rmcfg0 | 0.587 s | 13.0 s | **LENTO** |
| oslc.py:451 | rmcfg1 | 0.810 s | 19.2 s | **LENTO** |
| oslc.py:454 | rmcfg2 | 2.147 s | 54.5 s | **LENTO** |
| oslc.py:834 | fa834, fa834b | 0.005 s | 0.030 s | **OK** |
| oslc.py:840 | nbs857 | 0.000 s | 0.000 s | **OK** |
| oslc.py:1011 | const:oslc.py:1011 _STATE_URI_RE | 0.006 s | 0.029 s | **OK** |
| oslc.py:1016 | const:oslc.py:1016 split [/#] | 0.122 s | 0.624 s | **OK** |
| oslc.py:1175 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1176 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1177 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1178 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1179 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1180 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1182 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1184 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1185 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1186 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1286 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1289 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1292 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1293 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1297 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1299 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1300 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1301 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1302 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1312 | san:<img , san:<input , san:<meta  (+15) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1313 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1314 | san:<img , san:<input , san:<meta  (+14) | 0.165 s | 0.861 s | **OK** |
| oslc.py:1433 | cf1433 | 5.444 s | >60 s | **LENTO** |
| oslc.py:1434 | rt1434 | 1.974 s | 48.5 s | **LENTO** |
| oslc.py:1437 | cr1437 | 1.899 s | 50.2 s | **LENTO** |
| oslc.py:1494 | id1494, id1494b | 0.951 s | 26.3 s | **LENTO** |
| oslc.py:1807 | pt1807 | 0.589 s | 15.1 s | **LENTO** |
| oslc.py:1846 | desc1846 | 2.500 s | 60.0 s | **LENTO** |
| oslc.py:1847 | desc1846 | 2.500 s | 60.0 s | **LENTO** |
| oslc.py:1848 | desc1846 | 2.500 s | 60.0 s | **LENTO** |
| oslc.py:1852 | desc1846b | 0.970 s | 22.6 s | **LENTO** |
| oslc.py:1860 | title1860 | 0.831 s | 20.6 s | **LENTO** |
| oslc.py:1865 | title1860 | 0.831 s | 20.6 s | **LENTO** |
| oslc.py:1939 | prop1939 | 8.640 s | >60 s | **LENTO** |
| oslc.py:1940 | ttl1940 | 0.893 s | 22.9 s | **LENTO** |
| oslc.py:1992 | const:oslc.py:1992 _FACTORY_TITLE_RE, ccm5 | 0.005 s | 0.028 s | **OK** |
| oslc.py:2006 | ccm0 | 6.150 s | >60 s | **LENTO** |
| oslc.py:2007 | ccm1 | 1.598 s | 42.9 s | **LENTO** |
| oslc.py:2008 | ccm2 | 1.896 s | 49.4 s | **LENTO** |
| oslc.py:2011 | EXTRA2011 | 0.000 s | 0.002 s | **OK** |
| oslc.py:2014 | ccm3 | 1.554 s | 39.3 s | **LENTO** |
| oslc.py:2015 | ccm4 | 1.493 s | 38.0 s | **LENTO** |
| oslc.py:2049 | shp2049 | 5.995 s | >60 s | **LENTO** |
| oslc.py:2050 | shp2050 | 1.616 s | 41.0 s | **LENTO** |
| oslc.py:2053 | shp2053 | 1.397 s | 36.6 s | **LENTO** |
| oslc.py:2139 | const:oslc.py:2139 _PLAIN_SEGMENT_RE | 0.014 s | 0.067 s | **OK** |
| oslc.py:2220 | av0, av1 | 8.291 s | >60 s | **LENTO** |
| oslc.py:2221 | av3 | 0.000 s | 0.002 s | **OK** |
| oslc.py:2222 | av2 | 0.895 s | 20.9 s | **LENTO** |
| oslc.py:2227 | av4 | 0.648 s | 18.6 s | **LENTO** |
| oslc.py:2281 | der2281, der2281b | 0.031 s | 0.154 s | **OK** |
| oslc.py:2285 | der2285 | 0.001 s | 0.005 s | **OK** |
| oslc.py:2341 | fol2341 | 1.218 s | 31.6 s | **LENTO** |
| oslc.py:2379 | const:oslc.py:2379 _TEAM_URL_PATH_RE | 0.000 s | 0.003 s | **OK** |
| oslc.py:2380 | const:oslc.py:2380 _SAFE_ID_RE | 0.013 s | 0.067 s | **OK** |
| oslc.py:2490 | const:oslc.py:2490 _UNASSIGNED_RE | 0.023 s | 0.115 s | **OK** |
| oslc.py:2491 | const:oslc.py:2491 _WORKFLOW_STATE_RE | 0.006 s | 0.029 s | **OK** |
| oslc.py:2492 | const:oslc.py:2492 _WORKITEM_ID_RE | 0.013 s | 0.069 s | **OK** |
| oslc.py:2602 | PL2602 | 0.002 s | 0.002 s | **OK** |
| oslc.py:2603 | pl2603, pl2603b | 14.2 s | >60 s | **LENTO** |
| oslc.py:2837 | const:oslc.py:2837 _PROPERTY_PREFIX_RE | 0.005 s | 0.025 s | **OK** |
| oslc.py:2906 | const:oslc.py:2906 _FIELD_QNAME_RE | 0.003 s | 0.017 s | **OK** |
| oslc.py:2907 | const:oslc.py:2907 _FIELD_NAME_RE | 0.006 s | 0.030 s | **OK** |
| oslc.py:2924 | const:oslc.py:2924 _INTEGER_RE | 0.002 s | 0.013 s | **OK** |
| oslc.py:2925 | const:oslc.py:2925 _DECIMAL_RE | 0.021 s | 0.107 s | **OK** |
| oslc.py:3049 | const:oslc.py:3049 _ID_RE | 0.015 s | 0.079 s | **OK** |
| oslc.py:3050 | const:oslc.py:3050 _STATE_ID_RE | 0.089 s | 0.540 s | **OK** |
| profile.py:25 | const:profile.py:25 _SAFE_NAME | 0.009 s | 0.044 s | **OK** |
| qm.py:51 | const:qm.py:51 _INT_RE | 0.003 s | 0.014 s | **OK** |
| qm_write.py:47 | const:qm_write.py:47 _QM_RESOURCE_PATH | 0.000 s | 0.003 s | **OK** |
| qm_write.py:48 | const:qm_write.py:48 _SAFE_PATH | 0.000 s | 0.003 s | **OK** |
| qm_write.py:52 | enc52, enc52b, enc52c | 0.003 s | 0.015 s | **OK** |
| qm_write.py:336 | qn336 | 0.001 s | 0.003 s | **OK** |

## Conserto (issue #88)

Um módulo, `src/elm_mcp/xmlscan.py`, substitui os 30 pontos; nenhum outro padrão foi tocado. Não usa `xml.etree` (o código comenta que ET falha no contexto stdio do MCP). A causa era `[^>]*` / `.*?` sem limite, reexecutado a partir de cada abertura sem `>` ou sem fechamento. O módulo mantém o resultado do regex antigo e muda só o custo:

- A abertura (`<prefixo:nome`) ainda é achada por regex (`<\w*:?nome`), que é linear porque as sequências de caracteres de palavra de duas aberturas não se sobrepõem.
- O próximo `>` é localizado uma vez e reaproveitado por todas as aberturas antes dele (`_Gt`).
- Quando uma abertura falha, todas as aberturas antes do mesmo `>` falham (a região delas é um subconjunto); sem `>` ou sem tag de fechamento adiante, todas as seguintes falham. O varredor salta em vez de tentar de novo.
- Valor de atributo: o regex antigo (`[^>]*rdf:resource="..."`, guloso) pegava o ÚLTIMO `rdf:resource="..."` antes do primeiro `>`, e o valor podia passar desse `>`. O varredor repete isso com `rfind`.
- `attachments.py`: `filename*` usa quantificadores limitados (charset e idioma `{0,64}`, espaços ao redor de `=` `{0,64}`).
- `_plain`: `<[^>]+>` virou `xmlscan.strip_tags` (do `<` ao primeiro `>`; `<>` não é tag). O primeiro `re.sub` (`<br\s*/?>|</(?:div|p)>`) é linear e ficou.

Pontos que seguem sem limite de tamanho/profundidade: o `.*?` antigo também não tinha, e impor um mudaria o resultado (blocos aninhados ou grandes). O tempo agora é linear no tamanho do texto.

Equivalência: `tests/test_xmlscan_equivalence.py` copia os regex antigos e compara com o código novo em fixtures do repo, XML à mão (legítimo e malformado) e ~10 mil entradas de fuzz com semente fixa.

Divergências conhecidas (nenhuma com entrada legítima):

1. `filename*`: charset ou idioma com mais de 64 caracteres, ou mais de 64 espaços ao redor de `=`, deixam de ser lidos como `filename*` (cai no `filename=`). Cabeçalhos reais têm `UTF-8` e uma tag de idioma curta.
2. `update_requirement`: um URI que contém `>` agora é recusado com ValueError antes de qualquer requisição (o regex antigo casava, pois o URI entrava como literal; `>` é inválido pela RFC 3986). Com vários `rdf:Description` e nenhum casando `rdf:about`, levanta erro em vez de editar o primeiro; o fallback vale só com um Description.

Resultado depois do conserto (mesma máquina, sem coverage): `tests/test_regex_timing.py` 44 passed em 0,3 s (antes: 30 failed, 14 passed em 225,9 s); suíte completa 1793 passed em 19 s. O maior tempo entre os 30 pontos é 0,077 s a 1 MB (`filename*`); medido a 1 MB: `strip_tags` 0,096 s e `_FILENAME_STAR` 0,22 s (64 espaços + 64 caracteres repetidos, crescimento linear); os demais ficam abaixo de 0,03 s (coluna 'depois'). As linhas `arquivo:linha` desta tabela referem-se ao `main` 798394d. As 31 linhas de tabela viram 30 casos de teste (oslc.py:1860 e 1865 compartilham um caso).
