# Platform Launcher

The primary local-development entrypoint is:

```powershell
python main.py
```

That single command starts the backend Docker stack from the resolved
Compose profile and then starts the Next.js dashboard as a separate
managed local child process. The launcher intentionally excludes the
Compose `web` service from the startup list so backend containers and
the local web developer workflow do not drift apart.

## Default behavior

`python main.py` performs these steps:

1. Resolves the repository root and launcher paths.
2. Validates Python, Docker, Docker daemon, Docker Compose, Node.js,
   npm, Compose config, and web dependency state.
3. Starts backend services from `infra/compose/docker-compose.dev.yml`
   or a supported override chain.
4. Waits for required backend health.
5. Starts `npm run dev -- --hostname <host> --port <port>` in `web/`.
6. Writes safe runtime metadata to `.tmp/platform-runtime.json`.
7. Streams web logs and keeps the launcher attached until shutdown.

## Common commands

```powershell
python main.py
python main.py start
python main.py stop
python main.py restart
python main.py status
python main.py health
python main.py doctor
python main.py logs
python main.py logs api
python main.py logs web
python main.py build
python main.py clean
python main.py clean --volumes --yes
```

There is no `all` subcommand. Normal full-platform development uses
`python main.py`.

## Supported profiles

Supported launcher profiles are derived from real Compose files:

- `development`
- `security`
- `secure-cohort-handshake`
- `secure-user-level-dp`
- `secure-hybrid-dp`
- `secure-adaptive-clipping`
- `masked-update-runtime`

## Shutdown behavior

By default, `Ctrl+C` stops the managed web process, brings down backend
containers started through the selected Compose project, preserves named
volumes, and removes the runtime-state file.

With `python main.py --keep-backend`, the web process is stopped but the
backend stack remains running and the runtime-state file is updated to
reflect that policy.
