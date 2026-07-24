# Sonata

Sonata is a product-independent Python workflow engine.

This repository starts from the generic workflow kernel extracted from
[`miciav/nanofaas`](https://github.com/miciav/nanofaas). Product-specific tasks,
remote execution, infrastructure providers, and release scenarios remain in
nanoFaaS.

## Packages

- Distribution: `sonata-engine`
- Import: `sonata_engine`

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run basedpyright
```

The v2 design and the nanoFaaS migration sequence are documented in
[`docs/plans/2026-07-24-release-on-workflow-engine.md`](docs/plans/2026-07-24-release-on-workflow-engine.md).
