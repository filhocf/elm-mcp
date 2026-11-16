# SPEC — Perfil por template de processo

**Status:** PLANNED · **Release:** v0.7.0 · **Guarda-chuva:** `SPEC-ewm-delta` F4 · **ADR:** ADR-003

## Meta
Fornecer, fora do pacote, os valores que a API não dá ou que mudam por instalação, organizados **por template de processo**
(fato observado: project areas do mesmo template têm tipos, propriedades e literais idênticos), com sobrescrita por project area.

## Conteúdo do perfil
- Valores padrão de campos obrigatórios por tipo (ex.: categoria, `filedAgainst`, iteração padrão).
- Pares válidos de enums dependentes (o servidor filtra um enum pelo valor de outro).
- Rótulos e aliases das ações de workflow (a API OSLC só devolve ids das ações).
- Estados que listagens devem ocultar por padrão.
- `rm_write_mode` por projeto (ver `SPEC-rm-correcoes`).

## Critério de aceite (EARS)
- O sistema DEVE carregar o perfil de arquivo externo ao pacote; SE não houver perfil, ENTÃO DEVE operar só com discovery.
- QUANDO uma project area for usada, o sistema DEVE aplicar o perfil do template dela e, depois, a sobrescrita da própria project area.
- QUANDO `create_workitem` for chamado sem um campo obrigatório que o perfil define, o sistema DEVE preencher a partir do perfil; SE nem o perfil tiver, ENTÃO DEVE recusar listando os campos faltantes (do shape).
- QUANDO uma ação for pedida por alias, o sistema DEVE traduzi-la pelo perfil.
- O sistema DEVE oferecer um comando que gera um rascunho de perfil a partir do discovery (tipos, obrigatórios, enums) para revisão humana.
- O sistema NÃO DEVE conter valores de instalação no pacote, nos testes ou na documentação pública.

## A decidir (mantenedor)
Local do perfil (`~/.elm-mcp/profiles/` ou fornecido pelo cliente) e formato (YAML ou JSON).
