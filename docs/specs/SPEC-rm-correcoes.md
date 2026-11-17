# SPEC — Correções RM e modo changeset

**Status:** PLANNED · **Release:** v0.5.1 (D3, D7, D8) e v0.6.0 (changeset) · **Guarda-chuva:** `SPEC-ewm-delta` F2

## Defeitos
| # | Tool | Sintoma observado | Correção |
|---|---|---|---|
| D3 | `list_folders` | 400 (exceção no servidor) com `public_rm:parent=<URI do component>` | Usar a URI da project area, como o service provider anuncia na query de pastas |
| D7 | `create_requirement` sem `type` | 403 CRRRS6404E (falta `instanceShape`) | `type` obrigatório (URI do shape ou título resolvido por `list_types("rm")`) |
| D8 | `create_requirement` | `identifier: null` | Reler o artefato criado e retornar `dcterms:identifier` |

## Modo changeset (projetos com Configuration Management)
Fato observado: escrita direta na stream é aceita quando a política do projeto permite. Criar changeset pode exigir permissão
própria. Logo, o modo é escolha de configuração, não detecção automática.

- Configuração `rm_write_mode`: `direct` (padrão) ou `changeset`, por projeto (perfil).
- No modo `changeset`: criar o changeset na stream com `oslc_config:component` no corpo; acompanhar a tarefa assíncrona se vier 202;
  escrever com `Configuration-Context` = changeset; entregar ao final; descartar em erro.
- Adicionar `delete_requirement(uri)` (DELETE; no modo changeset, dentro dele).

## Critério de aceite (EARS)
- QUANDO `list_folders(projeto)` for chamado, o sistema DEVE consultar pastas com o pai igual à project area do projeto.
- SE `create_requirement` for chamado sem `type`, ENTÃO o sistema DEVE recusar com `ToolError` listando os tipos de `list_types("rm")`.
- QUANDO um requisito for criado, o sistema DEVE retornar URI e identificador lidos do servidor.
- ONDE `rm_write_mode` for `changeset`, o sistema DEVE criar, escrever e entregar dentro de um changeset; SE qualquer passo falhar, ENTÃO DEVE descartar o changeset e informar a URI.
- ONDE `rm_write_mode` for `direct`, o sistema DEVE escrever sem changeset.
- QUANDO `delete_requirement` for chamado, o sistema DEVE excluir e confirmar por releitura (410 ou 404).

## A confirmar
- Fábrica e corpo de changeset e o método de entrega na 7.0.1 (o cliente de origem usa PATCH com `acceptedBy`; não verificado aqui por falta de permissão).
- Comportamento num projeto que exige changeset (esperar acesso ao segundo projeto de referência).

## Gates
G3 (respx) → G4 → G5 → aceitação viva: criar, atualizar e excluir requisito no modo `direct`; modo `changeset` só quando houver projeto e permissão para testar.
