# Local Development

## Primary workflow

Use the platform launcher for normal local development:

```powershell
python main.py
```

This command starts the backend Docker topology first and then launches
the Next.js dashboard locally.

## Prerequisites

- Python 3.11+
- Docker Desktop or a compatible Docker daemon
- `docker compose`
- Node.js with `npm`
- `web/node_modules` already installed, or use `python main.py --install-web`

## Useful commands

```powershell
python main.py --help
python main.py --install-web
python main.py --build
python main.py --keep-backend
python main.py status
python main.py health
python main.py logs --follow
python main.py stop
```

## Troubleshooting

- If a required port is already in use, the launcher exits with the
  conflicting service and port instead of killing unrelated processes.
- If the web process fails after backend startup, the launcher stops the
  backend again unless `--keep-backend` was requested.
- Runtime metadata is stored in `.tmp/platform-runtime.json`.
