# SPEC — Idempotência e checagem de duplicata na criação

**Status:** PLANNED (G0) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Onda:** W4 · **Depende de:** `SPEC-rm-consulta`

## Meta
Republicar a mesma especificação (ou repetir uma criação após erro de rede) não pode gerar artefato duplicado.

## Critério de aceite (EARS)
- QUANDO `create_requirement` for chamado, o sistema DEVE consultar, na mesma pasta e com o mesmo tipo, um requisito com o mesmo título antes de criar (consulta RM com contexto de configuração).
- QUANDO `create_workitem` for chamado, o sistema DEVE consultar um work item do mesmo tipo, título e pai antes de criar.
- O parâmetro `if_exists` DEVE aceitar `error` (padrão: recusar com `ToolError` listando URI e identificador do existente), `return` (devolver o existente com `created: false`, sem escrever) e `create` (criar mesmo assim).
- SE a consulta de duplicata falhar (erro de rede ou de permissão), ENTÃO o sistema DEVE recusar a criação com `ToolError` explicando que a checagem não pôde ser feita, exceto se `if_exists="create"`.
- O retorno de criação DEVE incluir `created: true|false`.
- O sistema NÃO DEVE reenviar o POST de criação automaticamente após falha ambígua (timeout depois do envio); DEVE reconsultar a duplicata e só então decidir.

## Notas
Mudança incompatível deliberada: o padrão `error` recusa criação de título duplicado na mesma pasta/tipo. Registrar em `### Changed` do CHANGELOG.
Normalização do título para comparação: `strip`, colapso de espaços e casefold. A SPEC não usa hash de conteúdo: título + tipo + pasta identificam o artefato na prática, e hash do corpo daria falso negativo em edição.

## Gates
G3 (respx: sem duplicata cria; duplicata com cada `if_exists`; consulta falha; timeout seguido de reconsulta) → G4 → G5 → aceitação viva (somente leitura: a consulta de duplicata).
