# SPEC — Delta de funcionalidades (cliente ELM de equipe → core genérico + perfil)

**Status:** PLANNED (G0) · **Release:** proposta v0.6.0 a v0.9.0, por fatia (ver "Fatias") · **RFC-mãe:** RFC-001 §5 · **ADR:** ADR-003
**Pesquisa:** `conhecimentos-de-ia/ferramentas/elm-mcp/delta-cadu-ia-ewm-rm.md` · **Classe:** guarda-chuva. Cada fatia vira uma SPEC atômica e um PR quando iniciar.

## Meta

Trazer para o elm-mcp as funcionalidades que um cliente MCP de ALM usado em produção por uma equipe já tem e o elm-mcp ainda não tem,
separando o que é recurso padrão do ELM (entra no core, genérico) do que é customização da instalação (entra no perfil, nunca no core).
O servidor de referência é uma instalação ELM 7.0.3 customizada, com Configuration Management habilitado no DOORS Next.

## Classificação (critério de corte)

| Classe | Significado | Destino |
|---|---|---|
| D | Recurso padrão ELM por API oficial (OSLC, Process REST, RPT) | core |
| DI | Recurso padrão ELM por API interna da Jazz (form-encoded, sem garantia de suporte IBM) | core, em módulo isolado, atrás de flag |
| C | Valor, ID ou regra que existe só numa instalação | perfil por project area |
| N | Convenção de processo de uma equipe, não é recurso do ALM | fora do elm-mcp (skill ou cliente) |

## Fatias

| Fatia | Conteúdo | Classe | Esforço | Depende de |
|---|---|---|---|---|
| S0 | Verificação no servidor de referência (somente leitura) | — | 🟢 | acesso ao ALM |
| S1 | Comentários, filtros estruturados, `check_connection`, re-parentar, link WI↔WI, `set_enum_field`, transição por ação descoberta | D | 🟡 | S0 |
| S2 | RM com Configuration Management: changeset, `Configuration-Context`, `delete_requirement` | D | 🔴 | S0 |
| S3 | Anexos (listar, baixar, anexar) e folha de horas (listar, lançar, remover) | DI | 🟡 | S1 |
| S4 | Perfil por PA: carregador e esquema (ADR-003) | C | 🟡 | S0 |
| S5 | Discovery estendido: team areas, timelines, ações de workflow, enums de tempo | D | 🟢 | — |

Fora do escopo desta SPEC: convenções de equipe (sinal de aprovação em comentário, regra de pontos, composição "iniciar tarefa"),
extração de texto de anexos (docx/pptx/xlsx/pdf) e criação de plano de iteração por API interna.

## Critério de aceite (EARS)

### S0 — verificação
- QUANDO a verificação rodar no servidor de referência, o sistema DEVE registrar em documento: (a) se `create_requirement` falha em projeto RM com Configuration Management; (b) as ações de workflow que `rtc_cm:workflowActions` devolve para work item de tipo story e de tipo task; (c) os campos obrigatórios e `allowedValues` de cada tipo via `list_fields`; (d) se `rtc_cm:estimate` e links entre work items são aceitos por OSLC.
- A verificação NÃO DEVE escrever no servidor.

### S1 — core OSLC
- QUANDO `add_comment(id, text)` for chamado, o sistema DEVE publicar o comentário via OSLC e retornar o identificador; QUANDO `list_comments(id)` for chamado, DEVE retornar autor, data e texto em ordem cronológica.
- QUANDO `list_workitems` receber `owner`, `type`, `state` ou `planned_for`, o sistema DEVE filtrar por esses campos no servidor, mantendo `query` livre como opção.
- QUANDO `transition_workitem` receber um identificador de ação, o sistema DEVE consultar `rtc_cm:workflowActions` do item e executar a ação cujo identificador coincide; SE nenhuma coincidir, ENTÃO DEVE recusar com `ToolError` listando as ações disponíveis para aquele item.
- O sistema NÃO DEVE usar o mapa fixo de estados de task como fallback para itens de outro workflow.
- QUANDO `link_workitems(source, target, link_type)` for chamado com tipo suportado (`parent`, `blocks`, `depends_on`, `related`), o sistema DEVE criar o link e confirmar a presença na releitura; QUANDO `unlink_workitems` for chamado, DEVE remover só aquele link e preservar o restante do item.
- QUANDO `set_enum_field(id, attribute, literal)` for chamado, o sistema DEVE validar o literal contra os `allowedValues` do atributo e gravar com `If-Match`; SE o literal for inválido, ENTÃO DEVE recusar listando os válidos.
- QUANDO `create_workitem` for chamado para um tipo com campo obrigatório não informado, o sistema DEVE recusar com `ToolError` listando os campos faltantes (vindos do discovery), em vez de deixar o servidor recusar o save.

### S2 — RM com Configuration Management
- ONDE o projeto RM tiver Configuration Management habilitado, o sistema DEVE executar `create_requirement`, `update_requirement` e `delete_requirement` dentro de um changeset criado sobre a stream configurada, fazer o deliver ao final e descartar o changeset em caso de erro.
- ONDE o projeto não tiver Configuration Management, o sistema DEVE manter o comportamento atual (sem changeset).
- A stream e o componente DEVEM vir de configuração ou discovery, nunca de valor fixo no código.
- SE o deliver falhar, ENTÃO o sistema DEVE informar a URI do changeset para recuperação manual.

### S3 — API interna (DI)
- O código que usa endpoints internos DEVE ficar em módulo próprio e só DEVE ser exposto quando a flag `ELM_ENABLE_INTERNAL_API` estiver ativa; a documentação DEVE dizer que esses endpoints não têm suporte IBM e podem mudar entre versões.
- QUANDO `list_attachments(id)` for chamado, o sistema DEVE retornar nome, tamanho e identificador de cada anexo; QUANDO `download_attachment(id, attachment, dest)` for chamado, DEVE gravar o arquivo íntegro em `dest`; QUANDO `add_attachment(id, path)` for chamado, DEVE anexar e confirmar por releitura.
- QUANDO `log_time(id, hours, date, time_code)` for chamado, o sistema DEVE lançar a entrada na folha de horas do usuário autenticado e confirmar por releitura; `list_time(id)` e `remove_time(id, date)` DEVEM operar só sobre lançamentos do próprio usuário.

### S4 — perfil
- O sistema DEVE carregar um perfil por project area de arquivo externo ao pacote, com: valores de enums obrigatórios por tipo, categoria padrão (`filedAgainst`), aliases de ação de workflow, estados a excluir de listagens, códigos de tempo.
- QUANDO um perfil estiver ativo, `create_workitem` DEVE preencher os campos obrigatórios a partir dele; SE faltar valor, ENTÃO DEVE seguir a regra de campo obrigatório de S1.
- O sistema NÃO DEVE conter identificador específico de instalação no pacote, nos testes ou na documentação pública.

### S5 — discovery
- QUANDO `list_team_areas(project)` ou `list_timelines(project)` for chamado, o sistema DEVE retornar os itens via Process REST.
- QUANDO `list_workflow_actions(id)` for chamado, o sistema DEVE retornar as ações disponíveis para o item no estado atual.

## Gates
G0 (S0 como entrada) → G3 (respx por fatia; fixtures com valores neutros) → G4 → G5 (autor ≠ verificador). Uma SPEC atômica e um PR por fatia.

## Decisões em aberto (do mantenedor)
1. Onde vive o perfil: arquivo em `~/.elm-mcp/profiles/` ou pasta mantida pelo cliente (por exemplo, a skill do DAIA)?
2. APIs internas no pacote principal, atrás de flag, ou em pacote separado (`elm-mcp-internal`)?
3. Extração de texto de anexos: extra opcional do elm-mcp ou fica no cliente?
4. Contato com o mantenedor do cliente de origem antes de reutilizar a lógica.

## Fontes
- **F3**: cliente MCP de ALM em produção na equipe de origem (leitura de `ewm-server` e `rm-server`); detalhes e linhas em `delta-cadu-ia-ewm-rm.md`.
- **F2**: `pesquisa-apis-elm.md` (limites do OSLC para comentários, anexos e Configuration Management).
