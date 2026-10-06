# Mestre das Odds — portfolio demo

An asynchronous multi-source sports odds processing system built with Python,
FastAPI, SQLite, React and TypeScript.

This repository is a **backend and data engineering portfolio case**. The default
web app runs with fictional local data. It needs no account, API key or external
service, and it does not place bets. Legacy collectors remain available as code
examples; their external endpoints are not required for the demo and may change.

## Run locally (Windows PowerShell)

From the repository root, in one terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repository root:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open **http://127.0.0.1:5173/dashboard**. The API is at
**http://127.0.0.1:8000/docs**. No `.env` file is required. Python 3.11+ and a
current Node.js installation are needed.

The demo creates `backend/portfolio.db` on first launch. Preferences and
operation history persist there; the file is ignored by Git. The displayed
signal is calculated from fictional odds in `backend/app/demo.py`. To reset
local preferences and operation history, stop the API and remove that database
file. The app has no login and is intended to run on `127.0.0.1` only.

## Architecture

```text
Fictional odds or optional external collectors
    -> canonical market models -> event/market matching
    -> arbitrage engine -> FastAPI -> React dashboard
                               \-> SQLite (preferences and operations)
```

- `backend/src/odds_arb/`: collectors, normalization, event matching and
  arbitrage logic. The demo reuses the deep market detector from this package.
- `backend/app/`: FastAPI routes and the SQLite repository for local user data.
- `frontend/src/`: React dashboard, filters, calculation and operation history.
- `backend/tests/` and frontend `*.test.*`: core, adapter, API and UI tests.

**Tradeoffs:** a single local SQLite file keeps the demo easy to reproduce.
There is no multi-user authentication. The external collectors are isolated
from the default demo because their endpoints are outside this project's
control. The fictional signal is labeled in the UI and has no external link.

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check app src tests
..\.venv\Scripts\python.exe -m mypy --strict app src
```

```powershell
cd frontend
npm test
npm run build
```

## GitHub description

Credential-free sports odds data engineering demo: asynchronous Python
collectors, heterogeneous data normalization, event matching, arbitrage
calculation, FastAPI, local SQLite persistence and a React dashboard.

## Short LinkedIn text

Transformei um projeto antigo em um case de engenharia de software: backend
assíncrono em Python, normalização de dados de múltiplas fontes, matching de
eventos, cálculo de arbitragem, API FastAPI e dashboard React. A versão de
portfólio roda com dados fictícios e SQLite local, sem credenciais ou serviços
externos. Os testes cobrem o núcleo de processamento e o fluxo da interface.
