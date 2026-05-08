# Architecture — elm-mcp

## Overview

MCP server for IBM Engineering Lifecycle Management (ELM). Exposes Jazz/ELM work items, requirements, and projects as MCP tools for AI agent consumption.

## Stack

- Python 3.11+
- MCP SDK
- elmclient (IBM ELM Python client library)

## Tools

| Tool | Description |
|------|-------------|
| **list_projects** | List all ELM project areas |
| **search_work_items** | Query work items with filters (type, state, owner) |
| **get_work_item** | Get full work item details by ID |
| **create_work_item** | Create a new work item |
| **update_work_item** | Update work item fields |
| **list_requirements** | List requirements from DOORS Next |
| **get_requirement** | Get requirement details |
| **link_artifacts** | Create traceability links between artifacts |

## Architecture

```
┌─────────────┐     MCP Protocol      ┌──────────────┐
│  AI Agent   │ ◄──────────────────► │  elm-mcp     │
│  (Claude)   │                        │  MCP Server  │
└─────────────┘                        └──────┬───────┘
                                              │ elmclient
                                              ▼
                                       ┌──────────────┐
                                       │  IBM ELM     │
                                       │  (Jazz/OSLC) │
                                       └──────────────┘
```

## Key Design Decisions

- **elmclient** handles OSLC/Jazz authentication (form-based, OAuth) and resource navigation
- Work items mapped to simplified JSON structures for LLM consumption
- Pagination handled internally; tools return bounded result sets
- OSLC resource shapes used for field discovery

## Configuration

Environment variables:

```
ELM_SERVER_URL=https://elm.example.com
ELM_USERNAME=admin
ELM_PASSWORD=secret
ELM_PROJECT_AREA=MyProject
```

## Directory Structure

```
src/
├── server.py           # MCP server entry point
├── elm_client.py       # elmclient wrapper
├── tools/
│   ├── projects.py     # Project area operations
│   ├── work_items.py   # Work item CRUD
│   ├── requirements.py # DOORS Next requirements
│   └── links.py        # Traceability links
└── models/
    └── types.py        # Shared types and mappings
```
