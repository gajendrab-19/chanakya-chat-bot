"""
Chanakya University Admission Bot — Secure Backend
FastAPI + Groq (llama-3.3-70b-versatile) + live CU website scraping

Security fixes applied:
  [1]  API key loaded from environment, never hardcoded
  [2]  CORS restricted to explicit allowed origins only
  [3]  Rate limiting: 10 chat/min, 30 health/min per IP
  [4]  Input validation: message max 500 chars, history max 10 turns
  [5]  History role whitelist: only 'user' and 'assistant' accepted
  [6]  Domain-pinned scraper: only fetches chanakyauniversity.edu.in
  [7]  No open redirects: off-domain redirects are blocked
  [8]  Bounded cache: max 200 entries, LRU-style eviction
  [9]  Specific exception handling: no bare except clauses
  [10] Internal errors never leaked to client responses
  [11] Swagger/ReDoc UI disabled in production
  [12] Structured logging with no sensitive data in logs
"""

import os
import re
import json
import time
import hashlib
import logging
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from dotenv import load_dotenv

# ── Load .env file if present ────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

# ── Logging (structured, no secrets) ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chanakya-bot")

# ── [FIX 1] Secrets from environment — never hardcoded ───────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    raise RuntimeError(
        "\n\n  GROQ_API_KEY is not set!\n"
        "  1. Copy backend/.env.example  →  backend/.env\n"
        "  2. Paste your key from https://console.groq.com\n"
        "  3. Restart the server.\n"
    )

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL    = os.environ.get("MODEL", "llama-3.3-70b-versatile")

# ── [FIX 2] CORS — explicit origins only, never wildcard ─────────────────────
_raw_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500,"
    "http://localhost:8000,http://127.0.0.1:8000"
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── [FIX 3] Rate limiter ──────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Chanakya University Admission Bot",
    docs_url=None,    # [FIX 11] Swagger disabled — no API map for attackers
    redoc_url=None,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # [FIX 2]
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

# ── Scraper config ────────────────────────────────────────────────────────────
CU_BASE   = "https://chanakyauniversity.edu.in"
CACHE_TTL = 3600
MAX_CACHE = 200       # [FIX 8] bounded cache
_cache: dict = {}

TOPIC_URLS = {
    "admission":     "/admission/",
    "fee":           "/admissions/fee-structure/",
    "scholarship":   "/admission/scholarships/chanakya-merit-cum-means-scholarship/",
    "engineering":   "/school-of-engineering/",
    "law":           "/school-of-law-governance-public-policy/",
    "management":    "/school-of-management-sciences/",
    "arts":          "/school-of-arts-humanities-social-sciences/",
    "bioscience":    "/school-of-biosciences/",
    "faculty":       "/faculty/",
    "campus":        "/campus/",
    "contact":       "/contact-us/",
    "placement":     "/placements/",
    "phd":           "/ph-d-programme-at-chanakya-university/",
    "pg":            "/2023/03/17/postgraduate-programs/",
    "ug":            "/2023/03/17/undergraduate-programs/",
    "international": "/global-engagement/for-students/international-students/international-students-admission-at-chanakya-university/",
}

KEYWORDS = {
    r"fee|cost|price|tuition|lakh|rupee":        "fee",
    r"scholarship|financial|merit|cset|waiver":  "scholarship",
    r"btech|b\.tech|engineer|cse|ece|computer":  "engineering",
    r"law|llb|legal":                            "law",
    r"mba|management|bba|business":              "management",
    r"arts|humanities|psychology|economics":     "arts",
    r"bio|biotech|life science|bioscience":      "bioscience",
    r"phd|doctoral|doctorate|research":          "phd",
    r"pg|postgrad|msc|mca|masters|mtech":        "pg",
    r"ug|undergrad|bachelor|bsc|bcom|bca":       "ug",
    r"faculty|professor|teacher|staff":          "faculty",
    r"campus|hostel|library|infrastructure":     "campus",
    r"international|foreign|nri|overseas":       "international",
    r"placement|job|recruit|salary|package":     "placement",
    r"contact|address|phone|email|location":     "contact",
    r"cupp|entrance|exam|test|kcet|jee|clat":    "admission",
}

# ── [FIX 8] Bounded cache helpers ────────────────────────────────────────────
def cache_get(k: str):
    entry = _cache.get(k)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None

def cache_set(k: str, d: str):
    if len(_cache) >= MAX_CACHE:                  # evict oldest entry
        oldest = min(_cache, key=lambda x: _cache[x]["ts"])
        del _cache[oldest]
    _cache[k] = {"data": d, "ts": time.time()}

# ── [FIX 6 + 7] Domain-pinned scraper, no open redirects ─────────────────────
async def scrape(url: str) -> str:
    # Hard guard — only the CU domain is ever fetched
    if not url.startswith(CU_BASE):
        logger.warning("Blocked off-domain fetch attempt: %s", url)
        return ""

    key = hashlib.md5(url.encode()).hexdigest()
    hit = cache_get(key)
    if hit:
        return hit

    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=False,              # [FIX 7] handle redirects manually
            headers={"User-Agent": "ChanakYa-AdmissionsBot/1.0"},
        ) as c:
            r = await c.get(url)

            # Allow redirect only if destination stays on CU domain
            if r.is_redirect:
                location = r.headers.get("location", "")
                if not location.startswith(CU_BASE):
                    logger.warning("Off-domain redirect blocked: %s → %s", url, location)
                    return ""
                r = await c.get(location)

            r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["nav", "footer", "script", "style", "header", "iframe", "noscript"]):
            tag.decompose()
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(class_="entry-content")
            or soup.body
        )
        text = main.get_text("\n", strip=True) if main else ""
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = text[:5000]
        cache_set(key, text)
        return text

    # [FIX 9] Specific exception handling — no bare except
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP %s scraping %s", e.response.status_code, url)
        return ""
    except httpx.TimeoutException:
        logger.warning("Timeout scraping %s", url)
        return ""
    except httpx.RequestError as e:
        logger.warning("Request error scraping %s: %s", url, type(e).__name__)
        return ""

def detect_topic(q: str) -> str:
    q = q.lower()
    for pattern, topic in KEYWORDS.items():
        if re.search(pattern, q):
            return topic
    return "admission"

async def get_context(query: str):
    topic = detect_topic(query)
    url   = CU_BASE + TOPIC_URLS.get(topic, "/admission/")
    pages = await asyncio.gather(scrape(url), scrape(CU_BASE + "/admission/"))
    parts = [p for p in pages if p and len(p) > 100]
    return "\n\n---\n\n".join(parts), url

# ── [FIX 4 + 5] Validated, bounded request models ────────────────────────────
ALLOWED_ROLES = {"user", "assistant"}

class HistoryItem(BaseModel):
    role: str
    content: str = Field(..., max_length=1000)

    @field_validator("role")
    @classmethod
    def role_must_be_safe(cls, v):
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Invalid role '{v}'")
        return v

class ChatReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: Optional[list[HistoryItem]] = Field(default=[], max_length=10)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/chat")
@limiter.limit("10/minute")                      # [FIX 3]
async def chat(request: Request, req: ChatReq):
    context, source = await get_context(req.message)

    system = f"""You are Sonali, the friendly Admission Assistant for Chanakya University, Bangalore.

STRICT RULES:
- Answer ONLY using the CONTEXT below. Do NOT use your own knowledge about any university.
- If context lacks the answer say: "I don't have that detail right now — please contact admissions@chanakyauniversity.edu.in or call +91 8550855092"
- Keep answers SHORT: 2-3 sentences max. Be direct and friendly.
- Never mention other universities. Never guess.

CONTEXT FROM CHANAKYA UNIVERSITY WEBSITE:
{context}"""

    messages = [{"role": "system", "content": system}]
    # [FIX 5] Only safe roles pass through to the LLM
    for turn in req.history[-4:]:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": req.message})

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST", GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":       MODEL,
                        "messages":    messages,
                        "stream":      True,
                        "max_tokens":  200,
                        "temperature": 0.1,
                    }
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk.strip() == "[DONE]":
                            break
                        try:
                            d     = json.loads(chunk)
                            token = d["choices"][0]["delta"].get("content", "")
                            if token:
                                yield f"data: {json.dumps({'token': token})}\n\n"
                        except (KeyError, json.JSONDecodeError):
                            continue

            yield f"data: {json.dumps({'done': True, 'source': source})}\n\n"

        # [FIX 9 + 10] Specific exceptions, generic client message, full detail in logs
        except httpx.TimeoutException:
            logger.error("Groq API timeout for message (length=%d)", len(req.message))
            yield f"data: {json.dumps({'token': 'Sorry, the response timed out. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': source})}\n\n"
        except httpx.RequestError as e:
            logger.error("Groq API request error: %s", type(e).__name__)
            yield f"data: {json.dumps({'token': 'Sorry, something went wrong. Please try again.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': source})}\n\n"
        except Exception:
            logger.exception("Unexpected error in /chat stream")
            yield f"data: {json.dumps({'token': 'Sorry, an unexpected error occurred.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'source': source})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
@limiter.limit("30/minute")
async def health(request: Request):
    return {"status": "ok", "model": MODEL, "provider": "Groq"}


@app.get("/suggestions")
@limiter.limit("30/minute")
async def suggestions(request: Request):
    return {"questions": [
        "What are the BTech fees?",
        "How do I apply for admission?",
        "What scholarships are available?",
        "Tell me about MBA program",
        "What is CUPP exam?",
        "What law courses are offered?",
        "Is hostel available?",
        "What are the placement stats?",
    ]}
