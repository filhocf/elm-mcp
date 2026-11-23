# SPEC — QM write (escrita no ETM): casos, planos, suítes, scripts, vínculos

**Status:** G0 feito (evidência de 16/nov e de leitura viva 23/nov); aceitação viva de ESCRITA pendente (escrita suspensa) · **Release:** v0.6.0 · **RFC-mãe:** RFC-001 §5 · **Depende de:** `SPEC-qm-leitura`, `SPEC-oslc-links`
**Classe:** entregável de produto — fecha o ciclo RM→QM→CM

## Meta
Escrita no domínio QM (ETM) via OSLC QM 2.0: criar e manter os recursos de teste e vinculá-los a requisitos e work items.

## Evidência (servidor de referência, ETM 7.0.1 SR1)
- Criar TestCase (16/nov, escrita real): POST RDF/XML na fábrica do tipo, só com `dcterms:title`, responde 201 com `Location` (o servidor preenche estado inicial e id curto). A lista de "obrigatórios" do shape inclui propriedades de sub-shapes e não reflete o mínimo real.
- Atualizar (16/nov): PUT do RDF completo com `If-Match`: 200; ETag obsoleto: 412.
- Excluir (16/nov): DELETE: 200; leitura posterior: 404.
- Leitura viva (23/nov): `services.xml` publica `oslc:CreationFactory` para os mesmos tipos das consultas (resourceType em dois namespaces, `oslc_qm` e `rqm`), com a URL de criação = a base do recurso. Um TestCase individual (RDF/XML) traz `ETag` e os links `oslc_qm:validatesRequirement`, `oslc_qm:testsChangeRequest`, `oslc_qm:usesTestScript`. Fixtures: `tests/fixtures/qm_testcase_one.xml`, `qm_creation_factories.xml`.
- O QM não tinha Configuration Management no projeto testado.

## Decisão do gerente (Gate -1): escopo da v0.6.0
Só o que tem evidência de escrita real entra:
- **Entra:** `create_qm_resource` para `TestCase` (verificado), e, pelo mesmo padrão OSLC de fábrica e sem campos extras, `TestPlan`, `TestSuite` e `TestScript` (somente título; marcados "a confirmar na aceitação viva"); `update_qm_resource`; `delete_qm_resource`; `link_test_case` (TestCase → requisito `validatesRequirement`; TestCase → work item `testsChangeRequest`) e `unlink_test_case`.
- **ADIADO:** criação de `TestExecutionRecord` e `TestResult` (veredito, pontos, vínculo ao TCER/TestCase) e `TestScript` com passos nativos: nenhum teve escrita verificada e dependem de propriedades `rqm` fora do OSLC padrão. Condição para destravar: escrita em item descartável + captura do payload.

## Critério de aceite (EARS)
- QUANDO `create_qm_resource` for chamado com `type` e título, o sistema DEVE descobrir a fábrica de criação pelo `oslc:resourceType` (nome local, nos dois namespaces) do `services.xml`, enviar POST RDF/XML só com `dcterms:title` (e `dcterms:description` quando informada) e retornar `uri` e `short_id` lidos do servidor por releitura.
- SE o tipo for TestExecutionRecord, TestResult ou outro fora da lista suportada, ENTÃO o sistema DEVE recusar com `ToolError` listando os tipos suportados e dizendo que os demais estão adiados.
- QUANDO um recurso for atualizado, o sistema DEVE reler o recurso, alterar só título/descrição, e enviar PUT do RDF completo com `If-Match`; SE o servidor responder 412, ENTÃO DEVE levantar `ToolError` de conflito (gate `[conflito]`).
- QUANDO um TestCase for vinculado a um requisito ou work item, o sistema DEVE reler o recurso, acrescentar o link preservando os demais, enviar o PUT completo com `If-Match` e confirmar na releitura (conjunto final == esperado); `unlink_test_case` DEVE remover só aquele alvo.
- QUANDO `delete_qm_resource` for chamado, o sistema DEVE excluir e confirmar por releitura (404 ou 410); SE a leitura de confirmação der 401/403, ENTÃO DEVE informar "exclusão não confirmada".
- O sistema DEVE aplicar mesma origem, diagnóstico de gate e o bloqueio de URIs fora do ELM já existentes, e DEVE sanear o título (sem caracteres de controle; XML escapado).
- ONDE o ETM exigir dado fora do OSLC, o sistema DEVE documentar a limitação e NÃO DEVE falhar em silêncio.
- O sistema NÃO DEVE reenviar o POST de criação após falha ambígua; DEVE reconsultar duplicata (título + tipo) como em `SPEC-idempotencia`, com `if_exists`.

## Aceitação viva (PENDENTE: escrita suspensa)
Em itens descartáveis: criar caso, vincular a um requisito e a um work item de teste, atualizar título, excluir; reler cada passo.

## Gates
G3 (respx por recurso + vínculo + 412 + duplicata) → G4 → G5 → aceitação viva (quando a escrita for liberada).
