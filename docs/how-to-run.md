# How to run this project

The app is two processes on your computer: a Python backend on port **8000** and a Node/Vite UI on port **5173**. Open them in two terminals. Nothing here is deployed to the internet.

You need **Git**, **Python 3.11+**, and **Node 20+**. Check each tool before installing it.

## 1. Check what is already installed

In PowerShell (Windows) or a terminal (macOS/Linux):

```powershell
git --version
python --version
node --version
npm --version
```

On macOS/Linux, if `python` is missing, also try:

```bash
python3 --version
```

| Command | You are ready when you see |
|---|---|
| `git --version` | `git version 2.x` or similar |
| `python --version` (or `python3 --version`) | `Python 3.11` or newer (3.12/3.13 is fine) |
| `node --version` | `v20` or newer |
| `npm --version` | a version number (comes with Node) |

If a command prints *not recognized*, *command not found*, or a version that is too old, install that tool from the links below, then **close and reopen the terminal** and check again.

Windows note: the Microsoft Store `python` stub can pretend Python is installed and then fail. If `python --version` opens the Store instead of printing a version, install Python from python.org (below) and tick **Add python.exe to PATH**.

## 2. Install anything that is missing

Use the official downloads:

| Tool | What to get | Download |
|---|---|---|
| Git | Latest 64-bit installer | [https://git-scm.com/downloads](https://git-scm.com/downloads) |
| Python | **3.11 or newer**, 64-bit | [https://www.python.org/downloads/](https://www.python.org/downloads/) |
| Node.js | **20 LTS or newer** (includes npm) | [https://nodejs.org/en/download](https://nodejs.org/en/download) |

Per operating system:

- **Windows:** Git for Windows from git-scm.com. Python: during setup, enable **Add python.exe to PATH**. Node: Windows Installer (.msi), LTS.
- **macOS:** the same sites, or [Git for macOS](https://git-scm.com/downloads/mac), [Python for macOS](https://www.python.org/downloads/macos/), [Node for macOS](https://nodejs.org/en/download).
- **Linux:** distro packages are fine if they meet the versions, or use the same official pages. Git: [https://git-scm.com/downloads/linux](https://git-scm.com/downloads/linux).

Optional Windows package manager (if you already use it):

```powershell
winget install Git.Git
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
```

## 3. Get the code

If you already have this folder, skip clone and `cd` into it.

```powershell
git clone https://github.com/kbelchinski/biotech-stock-planner.git
cd biotech-stock-planner
```

## 4. Start the backend

**Windows (PowerShell):**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**macOS / Linux:**

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Leave this terminal open. You should see Uvicorn listening on `http://127.0.0.1:8000`.

If you see **WinError 10048** / “address already in use”, something is already bound to port 8000 (often a previous backend). Stop that process, or close that other terminal with Ctrl+C, then start again.

After you change `.env`, stop the backend with Ctrl+C and start it again. It only reads `.env` at startup.

## 5. Start the frontend

In a **second** terminal, from the repo root:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Click **Run scan**. No login is required.

Default mode is **demo**: synthetic BPIQ/Alpaca-shaped data, no API keys.

## 6. Optional: one process instead of two

From `frontend/`:

```powershell
npm install
npm run build
```

Then only the backend is needed. The UI is served at [http://127.0.0.1:8000](http://127.0.0.1:8000).

## 7. Optional: live data

Demo works with no keys. Live mode uses real BPIQ and Alpaca data and **never** falls back to demo.

1. Copy `.env.example` to `.env` in the **repo root** (not inside `backend/` or `frontend/`). Never commit `.env`.
2. Set `APP_MODE=live` and fill `BPIQ_API_KEY`, `BPIQ_ACCESS_TIER`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, and `ALPACA_ACCOUNT_TYPE` (must match the Alpaca account the keys belong to: `paper` or `live`).
3. Restart the backend, then check connectivity:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.diagnostics --labels
```

Optional extras (same `.env`; see comments in `.env.example`):

- **BPIQ MCP** (financials, insiders, funds on Company research): set `BPIQ_MCP_URL` (for example `https://mcp.bpiq.com/mcp`). Custom apps use your existing `BPIQ_API_KEY`. Restart, open **Data & integrations**, click **Discover tools**, test a **biotech** ticker, then save a confirmed capability mapping.
- **OpenAI “Explain and challenge”:** `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_INPUT_USD_PER_1M_TOKENS`, and `OPENAI_OUTPUT_USD_PER_1M_TOKENS` (prices from [OpenAI’s pricing page](https://developers.openai.com/api/docs/pricing) for that model).

Keep this app on localhost. It has no login.

## 8. Tests (optional)

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```

## If something fails

| Symptom | What to do |
|---|---|
| `git` / `python` / `node` not recognized | Install from the table in section 2, reopen the terminal, run the `--version` checks again |
| Python version below 3.11 | Install a newer 3.11+ from python.org and use that `python` |
| Node below 20 | Install current Node LTS |
| `npm error Missing script: "dev"` | You are in the repo root. `cd frontend` first |
| Backend cannot bind port 8000 | Stop the old Uvicorn process, then start again |
| UI cannot reach the backend | Backend must be running on 8000 before you use the site |
| Live scan says configuration problem | Keys missing or wrong; run diagnostics (section 7) |
