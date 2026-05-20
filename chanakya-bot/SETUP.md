# 🚀 Chanakya Bot — Setup Guide
## Steps to run after extracting the ZIP

---

## Prerequisites

| Tool | Check | Install |
|---|---|---|
| Python 3.10+ | `python3 --version` | https://python.org |
| pip | `pip3 --version` | included with Python |
| A browser | Any modern browser | – |
| Groq API key | Free at console.groq.com | see Step 1 |

---

## Step 1 — Get a free Groq API key

1. Go to **https://console.groq.com** and sign up (free, no credit card)
2. Click **"Create API Key"** → copy the key (starts with `gsk_…`)

---

## Step 2 — Configure your secrets

```bash
cd backend
cp .env.example .env
```

Open `.env` in any text editor and fill in:

```
GROQ_API_KEY=gsk_your_actual_key_here
ADMIN_PASS=choose_a_strong_password
```

> ⚠️ Never share or commit `.env` — it is already in `.gitignore`

---

## Step 3 — Install Python dependencies

```bash
# From the project root (chanakya-bot/)
pip3 install -r backend/requirements.txt
```

This installs: fastapi, uvicorn, httpx, beautifulsoup4, pydantic, slowapi, python-dotenv

---

## Step 4 — Start the backend

```bash
cd backend
uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

> Keep this terminal open while using the chatbot.

---

## Step 5 — Open the chatbot

**Option A — Direct open (easiest):**
Double-click `frontend/index.html` in your file manager.

**Option B — Via VS Code Live Server:**
Right-click `frontend/index.html` → "Open with Live Server"

The chat bubble appears in the bottom-right corner. Click it to start chatting with Sonali!

---

## Step 6 — (Optional) Start the Admin Panel

Open a **second terminal**:

```bash
cd admin
uvicorn admin_server:admin_app --host 127.0.0.1 --port 8001
```

Then open `admin/admin.html` in your browser.
- Your browser will prompt for a username and password
- Username: `admin` (or what you set in ADMIN_USER)
- Password: what you set in ADMIN_PASS in `.env`

---

## Quick Troubleshooting

| Problem | Fix |
|---|---|
| `GROQ_API_KEY is not set` | Check `backend/.env` exists and has your key |
| `ModuleNotFoundError` | Run `pip3 install -r backend/requirements.txt` |
| Chat shows "Backend offline" | Make sure uvicorn is running on port 8000 |
| 429 Too Many Requests | You sent more than 10 messages/minute — wait 60 seconds |
| Admin login fails | Check ADMIN_USER and ADMIN_PASS in `.env` |

---

## Project Structure

```
chanakya-bot/
├── frontend/
│   └── index.html          ← Chat UI (open in browser)
├── backend/
│   ├── server.py           ← FastAPI + Groq + scraper
│   ├── requirements.txt    ← Python dependencies
│   ├── .env.example        ← Template — copy to .env
│   └── .env                ← Your secrets (never commit this)
├── admin/
│   ├── admin.html          ← Admin dashboard UI
│   └── admin_server.py     ← Admin API (port 8001)
├── .gitignore
└── SETUP.md                ← This file
```
