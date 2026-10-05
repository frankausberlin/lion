# repolion

## Getting Started

```bash
uv sync
just check
```

## Development

```bash
just test       # run tests
just lint       # lint + type check
just fix        # auto-fix lint issues
just check      # full quality gate
```

## Release

```bash
just bump patch   # 0.1.0 → 0.1.1
git push origin main --tags
```

See [AGENTS.md](AGENTS.md) for AI agent guidelines.
