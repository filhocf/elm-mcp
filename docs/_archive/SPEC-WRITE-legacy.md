# SPEC: elm-alm-py Write Operations (CCM Work Items)

## Objetivo

Adicionar tools de escrita ao elm-alm-py para criar e atualizar work items no RTC (domínio CCM).

## Contexto

- Repo: ~/git/elm-alm-py
- Branch: criar `feat/write-workitems` a partir de `main`
- Padrão: seguir AGENTS.md (FastMCP tool, lógica em oslc.py, testes com respx)
- ELM 7.0.3 (configured via ELM_URL env var or ~/.elm_creds.json)
- Projeto alvo: "MEU IMOVEL RURAL (MIR)" (project area ID: `_MWxBEJB7Ee-fe_bes9r78g`)

## Tools a implementar

### 1. `create_workitem(project, title, type, description, parent_id, owner)`

Cria um work item no RTC via OSLC POST.

**Parâmetros:**
- `project: str` — nome do projeto (ex: "MEU IMOVEL RURAL (MIR)")
- `title: str` — título do work item (obrigatório)
- `type: str` — tipo: "task" ou "story" (default: "task")
- `description: str | None` — descrição HTML (opcional)
- `parent_id: str | None` — ID do work item pai (opcional)
- `owner: str | None` — username do owner (opcional, ex: "claudio.filho")

**Retorno:** `dict` com o work item criado (JSON do response)

**Implementação OSLC:**
1. Descobrir a Creation Factory URL:
   - Navegar: rootservices → catalog → ServiceProvider do projeto → services → CreationFactory com resourceType `http://open-services.net/ns/cm#ChangeRequest`
2. POST JSON no creation factory URL:
   ```json
   {
     "dcterms:title": "título",
     "dcterms:description": "descrição",
     "dcterms:type": "http://open-services.net/ns/cm#ChangeRequest",
     "rtc_cm:type": {"rdf:resource": "{elm_url}/ccm/oslc/types/{projectAreaId}/{typeId}"},
     "rtc_cm:com.ibm.team.workitem.linktype.parentworkitem.parent": [
       {"rdf:resource": "{elm_url}/ccm/resource/itemName/com.ibm.team.workitem.WorkItem/{parent_id}"}
     ]
   }
   ```
   Headers: `Content-Type: application/json`, `OSLC-Core-Version: 2.0`, `Accept: application/json`
3. Response 201 Created com Location header e body JSON do item criado

**Type IDs conhecidos (MIR):**
- task: `task`
- story (Item de Backlog): `com.ibm.team.apt.workItemType.story`

### 2. `update_workitem(id, title, description, status)`

Atualiza campos de um work item existente via OSLC PUT.

**Parâmetros:**
- `id: str` — ID do work item (obrigatório)
- `title: str | None` — novo título
- `description: str | None` — nova descrição
- `status: str | None` — novo status (não implementar transição de estado nesta versão — complexo demais)

**Implementação:**
1. GET o work item atual (para obter ETag)
2. PUT com os campos alterados + header `If-Match: {etag}`
3. Retornar o work item atualizado

**Nota:** status/state transition no RTC requer workflow action, não simples PUT. Deixar pra v2.

### 3. `add_child_workitem(parent_id, title, type, description, owner)`

Atalho para `create_workitem` com `parent_id` preenchido. Conveniência.

## Mudanças em oslc.py

Adicionar funções:

```python
async def _find_creation_factory(domain: str, project_url: str) -> str:
    """Find OSLC CreationFactory URL for a project."""
    # Similar a _find_query_base mas busca oslc:CreationFactory em vez de QueryCapability


async def create_resource(domain: str, project: str, payload: dict) -> dict:
    """POST a new resource to the creation factory."""


async def update_resource(uri: str, payload: dict) -> dict:
    """PUT updated fields to an existing resource (with ETag)."""
```

## Testes

Criar `tests/test_write.py`:
- Mock do discovery (rootservices → catalog → services → CreationFactory)
- Mock do POST 201 com body de resposta
- Mock do GET + PUT para update (com ETag)
- Testar validação de parâmetros (title obrigatório, type válido)
- Testar parent_id linkage

Usar `respx` para mock HTTP (padrão do projeto).

## Restrições

- NÃO alterar tools existentes (read-only)
- NÃO implementar state transitions (workflow actions) — complexo, v2
- NÃO implementar delete (perigoso)
- Manter compatibilidade com Python 3.11+
- Rodar `uv run ruff check src/ tests/` e `uv run pytest tests/ -v` antes de finalizar
- Seguir convenções do AGENTS.md (snake_case, async, httpx)

## Referência

- OSLC CM 2.0 spec: https://docs.oasis-open-projects.org/oslc-op/cm/v3.0/cm-v3.0.html
- RTC OSLC API: o `get_workitem` existente mostra o formato JSON de um work item
- CreationFactory está em: ServiceProvider → services XML → `oslc:CreationFactory`
