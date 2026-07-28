# Web Development

The web application is still developed with the real local Next.js
workflow, but it is now managed through the root platform launcher by
default:

```powershell
python main.py
```

The launcher sets:

- `HOST`
- `PORT`
- `FL_API_BASE_URL`
- `NEXT_PUBLIC_FL_API_BASE_URL`

and starts:

```powershell
npm run dev -- --hostname <host> --port <port>
```

in the `web/` directory.

## Standalone web checks

When working only on the web package, the existing commands remain
useful:

```powershell
cd web
npm run lint
npm run typecheck
npm run test
npm run build
```
