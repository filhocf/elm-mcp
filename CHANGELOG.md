# Changelog

## [0.2.0] - 2026-05-08

### Added
- Requirements management tools (`list_requirements`, `get_requirement`)
- Traceability link creation (`link_artifacts`)
- Work item creation and update (`create_work_item`, `update_work_item`)
- Pagination support for large result sets
- Field mapping for common ELM work item types

### Changed
- Improved error handling for OSLC authentication failures
- Work item search now supports multiple filter criteria

## [0.1.0] - 2026-03-01

### Added
- Initial release
- Project area listing (`list_projects`)
- Work item search and retrieval (`search_work_items`, `get_work_item`)
- elmclient integration with form-based auth
- MCP SDK server setup with stdio transport
