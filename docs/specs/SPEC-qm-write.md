# SPEC — QM write (escrita no ETM: test case/plan/script/execution/result)

**Status:** PLANNED (G0 parcial feito) · **Release:** v0.8.0 · **RFC-mãe:** RFC-001 §5 · **Depende de:** `SPEC-qm-leitura`, `SPEC-ccm-colaboracao` (links)
**Classe:** entregável de produto — fecha o ciclo RM→QM→CM

## Meta
Escrita no domínio QM (ETM) via OSLC QM 2.0: criar os recursos de teste e vinculá-los a requisitos e work items.

## Evidência (servidor de referência, ETM 7.0.1 SR1)
- Criar TestCase: POST RDF/XML na fábrica do tipo, só com `dcterms:title`, responde 201 com `Location` (o servidor preenche
  estado inicial e id curto). A lista de "obrigatórios" do shape inclui propriedades de sub-shapes e não reflete o mínimo real.
- Atualizar: PUT do RDF completo com `If-Match`: 200; ETag obsoleto: 412.
- Excluir: DELETE: 200; leitura posterior: 404.
- O QM não tinha Configuration Management no projeto testado.
- A confirmar: criação de TestScript com passos, TCER e TestResult (veredito), e criação de links `validatesRequirement` /
  `testsChangeRequest` por PUT.

## Escopo
- `create_test_case`, `create_test_plan`, `create_test_suite`, `create_test_script`, `create_test_execution_record`, `create_test_result`.
- `update_qm_resource(uri, campos)` (PUT com `If-Match`, reenviando o RDF relido).
- `delete_qm_resource(uri)`.
- Vínculos: TestCase → requisito (`validatesRequirement`), TestCase → work item (`testsChangeRequest`), TCER → TestCase (`runsTestCase`),
  TestResult → TCER/TestCase.

## Critério de aceite (EARS)
- QUANDO `create_test_case` for chamado com título, o sistema DEVE criar o recurso e retornar URI e id curto lidos do servidor.
- QUANDO um recurso for atualizado, o sistema DEVE usar PUT com `If-Match`; SE o servidor responder 412, ENTÃO DEVE levantar `ToolError` de conflito.
- QUANDO um caso de teste for vinculado a um requisito ou work item, o sistema DEVE gravar o link e confirmar na releitura.
- QUANDO `delete_qm_resource` for chamado, o sistema DEVE excluir e confirmar por releitura (404 ou 410).
- ONDE o ETM exigir dado fora do OSLC (ex.: passos de script em formato nativo), o sistema DEVE documentar a limitação e NÃO DEVE falhar em silêncio.

## Aceitação viva
Em itens descartáveis: criar caso, script, TCER e resultado; vincular caso a um requisito e a um work item de teste; excluir tudo.

## Gates
G0 (completar os itens "a confirmar") → G3 (respx por recurso + vínculo + 412) → G4 → G5 → aceitação viva.
