"""
Chanakya Admission Bot — Secure Admin Panel Backend
Run separately on port 8001.

Security fixes vs original:
  [1]  HTTP Basic Auth on every route — no open admin access
  [2]  Credentials loaded from environment — never hardcoded
  [3]  CORS restricted to localhost admin origins only
  [4]  Rate limiting on all endpoints
  [5]  admin.html loaded via absolute path — safe regardless of CWD
  [6]  Specific exception handling — no bare except
  [7]  timing-safe credential comparison (secrets.compare_digest)
  [8]  Failed login attempts logged with IP
"""

import os
import time
import secrets
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from dotenv import load_dotenv

# ── Load .env ────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

logger = logging.getLogger("chanakya-admin")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── [FIX 2] Credentials from environment ────────────────────────────────────
MAIN_API   = os.environ.get("MAIN_API_URL", "http://localhost:8000")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")

if not ADMIN_PASS:
    raise RuntimeError(
        "\n\n  ADMIN_PASS is not set!\n"
        "  Set it in backend/.env before starting the admin server.\n"
    )

# ── Rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── App ───────────────────────────────────────────────────────────────────────
admin_app = FastAPI(title="Chanakya Bot Admin", docs_url=None, redoc_url=None)
admin_app.state.limiter = limiter
admin_app.add_middleware(SlowAPIMiddleware)

# [FIX 3] CORS restricted to local admin origins only
admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── [FIX 1 + 7 + 8] HTTP Basic Auth — timing-safe ───────────────────────────
security = HTTPBasic()

def require_admin(request: Request, creds: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(creds.username.encode(), ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(creds.password.encode(), ADMIN_PASS.encode())
    if not (ok_user and ok_pass):
        # [FIX 8] Log failed attempt with IP, not password
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Failed admin login for user '%s' from %s", creds.username, client_ip)
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

# ── In-memory event log ───────────────────────────────────────────────────────
_logs: list = []

def log_event(event: str, data: dict):
    _logs.append({"ts": time.time(), "event": event, **data})
    if len(_logs) > 500:
        _logs.pop(0)

# ── [FIX 5] Absolute path to admin.html ──────────────────────────────────────
HTML_PATH = Path(__file__).parent / "admin.html"

# ── Routes ────────────────────────────────────────────────────────────────────
@admin_app.get("/", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def admin_ui(request: Request, _=Depends(require_admin)):
    if not HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return HTML_PATH.read_text(encoding="utf-8")


@admin_app.get("/api/stats")
@limiter.limit("30/minute")
async def stats(request: Request, _=Depends(require_admin)):
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{MAIN_API}/health")
            r.raise_for_status()
            health = r.json()
    # [FIX 6] Specific exception handling
    except httpx.HTTPStatusError as e:
        health = {"status": "error", "code": e.response.status_code}
    except httpx.TimeoutException:
        health = {"status": "timeout"}
    except httpx.RequestError:
        health = {"status": "unreachable"}

    return {
        "health":      health,
        "total_chats": len(_logs),
        "cache_keys":  0,
        "logs":        _logs[-50:],
    }
