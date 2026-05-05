# elm-mcp

MCP Server for IBM ELM (Engineering Lifecycle Management).

Exposes IBM ELM/Jazz work items, requirements, and projects as MCP tools.

## Install

```bash
uvx elm-mcp
# or
pip install elm-mcp
```

## Configuration

Create `~/.elm_creds.json`:
```json
{
  "username": "your.user",
  "password": "your.password"
}
```

## Requirements

- IBM ELM/Jazz server accessible (e.g., `https://alm.dataprev.gov.br`)
- Valid credentials with API access
