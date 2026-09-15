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

## 4. Create the `.env` file

The backend reads **one** file: `.env` in the **repo root** (next to `README.md`), not inside `backend/` or `frontend/`. Restart the backend after any change.

`.env` is listed in `.gitignore`, so Git will not commit it. This how-to file **is** committed. Do not paste API keys into markdown, even if the GitHub repo is private today: private can change, and keys in git history stay forever.

**Windows (PowerShell), from the repo root:**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

Then open `.env` and fill it in. Demo-only: you can leave keys empty and set `APP_MODE=demo`. Live data: set `APP_MODE=live` and the BPIQ + Alpaca keys.

Example of a live file (replace the `YOUR_…` lines with keys from BPIQ, Alpaca, and OpenAI — never commit those):

```env
# Restart the backend after changes.
LOG_LEVEL=DEBUG

# demo = synthetic fixtures, no keys required
# live = real BPIQ + Alpaca only; never falls back to demo
APP_MODE=live

# --- BPIQ Apex REST (https://app.bpiq.com/api-documentation) ---
# Header: Authorization: Token <key>
BPIQ_API_KEY=YOUR_BPIQ_API_KEY
# apex_paid = full catalyst horizon, 15 req/min
# apex_trial = next 30 days only, 10 req/min
BPIQ_ACCESS_TIER=apex_trial

# --- Alpaca (SIP daily bars + read-only asset lookup) ---
ALPACA_API_KEY_ID=YOUR_ALPACA_API_KEY_ID
ALPACA_API_SECRET_KEY=YOUR_ALPACA_API_SECRET_KEY
# Must match the account the keys belong to
ALPACA_ACCOUNT_TYPE=paper
ALPACA_RATE_LIMIT_PER_MIN=180

# --- BPIQ MCP (optional; financials / insiders / funds on Company research) ---
# Custom apps authenticate with BPIQ_API_KEY (not a separate OAuth client).
BPIQ_MCP_URL=https://mcp.bpiq.com/mcp
PUBLIC_BASE_URL=http://127.0.0.1:8000
UI_BASE_URL=http://localhost:5173

# --- Optional OpenAI "Explain and challenge" ---
# All four required or calls are refused. Prices = USD per 1M tokens for OPENAI_MODEL.
# https://developers.openai.com/api/docs/pricing
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
OPENAI_MODEL=gpt-5.4-mini
OPENAI_INPUT_USD_PER_1M_TOKENS=0.75
OPENAI_OUTPUT_USD_PER_1M_TOKENS=4.50
# AI_MONTHLY_BUDGET_USD=5
# AI_MAX_OUTPUT_TOKENS=1500

# --- Optional ---
# HTTP_TIMEOUT_SECONDS=20
# HTTP_MAX_RETRIES=3
```

| Variable | What to put |
|---|---|
| `APP_MODE` | `demo` or `live` |
| `BPIQ_API_KEY` | From [BPIQ](https://app.bpiq.com/api-documentation) |
| `BPIQ_ACCESS_TIER` | `apex_trial` or `apex_paid` |
| `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` | From the Alpaca dashboard for that account |
| `ALPACA_ACCOUNT_TYPE` | `paper` or `live` (must match the keys) |
| `BPIQ_MCP_URL` | Official endpoint: `https://mcp.bpiq.com/mcp` |
| `UI_BASE_URL` | Vite URL, usually `http://localhost:5173` (use `5174` only if the UI actually started on that port) |
| `OPENAI_API_KEY` | From OpenAI; optional |
| `OPENAI_MODEL` | A real API model id, e.g. `gpt-5.4-mini` (not a stub name) |
| `OPENAI_INPUT_USD_PER_1M_TOKENS` / `OPENAI_OUTPUT_USD_PER_1M_TOKENS` | Input and output prices for that model |

After MCP is configured: start the app, open **Data & integrations**, click **Discover tools**, test a **biotech** ticker, then save a confirmed capability mapping. Company research will not call those tools until you do.

## 5. Start the backend

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

Check live connectivity (after `.env` is filled and the venv exists):

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.diagnostics --labels
```

## 6. Start the frontend

In a **second** terminal, from the repo root:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Click **Run scan**. No login is required.

If `APP_MODE=demo`, scans use synthetic data. If `APP_MODE=live`, they use your BPIQ and Alpaca keys.

Keep this app on localhost. It has no login.

## 7. Optional: one process instead of two

From `frontend/`:

```powershell
npm install
npm run build
```

Then only the backend is needed. The UI is served at [http://127.0.0.1:8000](http://127.0.0.1:8000).

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
| Live scan says configuration problem | Keys missing or wrong; run diagnostics (section 5) |
