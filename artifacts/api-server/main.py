"""
Digitscoper Desktop Engine
==========================

Single-file FastAPI + SQLite application with an embedded dashboard.

Run the local web app:
    python main.py

Run the native desktop window:
    python main.py --desktop

Build a standalone desktop executable:
    pyinstaller --noconfirm --clean --onefile --name Digitscoper main.py

The database is created next to this file as digitscoper.db. Development seed
accounts are only created when ALLOW_DEV_SEED=1 is set in the environment;
they are never created in production. Set ADMIN_PASSWORD in the
environment before sharing the application to replace the development admin
password (admin123).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from collections import defaultdict

import bcrypt
import uvicorn

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    stripe = None  # type: ignore
    STRIPE_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas as pdf_canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.responses import HTMLResponse, RedirectResponse, Response


APP_DIR = Path(__file__).resolve().parent
DB_FILE = APP_DIR / "digitscoper.db"
PORT = int(os.environ.get("PORT", "8000"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
IPQS_ENDPOINT = "https://ipqualityscore.com/api/json/phone"

# --- Stripe billing configuration ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "").strip()
STRIPE_PROPLUS_PRICE_ID = os.environ.get("STRIPE_PROPLUS_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
if STRIPE_AVAILABLE and STRIPE_SECRET_KEY and stripe is not None:
    stripe.api_key = STRIPE_SECRET_KEY

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_PROPLUS = "pro_plus"
VALID_TIERS = (TIER_FREE, TIER_PRO, TIER_PROPLUS)

# Conservative email shape check used before any Stripe call.
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

STATE_AREA_CODES: dict[str, dict[str, Any]] = {
    "AL": {"name": "Alabama", "area_codes": ["205", "251", "256", "334", "938"]},
    "AK": {"name": "Alaska", "area_codes": ["907"]},
    "AZ": {"name": "Arizona", "area_codes": ["480", "520", "602", "623", "928"]},
    "AR": {"name": "Arkansas", "area_codes": ["327", "479", "501", "870"]},
    "CA": {"name": "California", "area_codes": ["209", "213", "279", "310", "323", "341", "350", "369", "408", "415", "424", "442", "510", "530", "559", "562", "619", "626", "628", "650", "657", "661", "669", "707", "714", "747", "760", "805", "818", "820", "831", "840", "858", "909", "916", "925", "935", "949", "951"]},
    "CO": {"name": "Colorado", "area_codes": ["303", "719", "720", "970", "983"]},
    "CT": {"name": "Connecticut", "area_codes": ["203", "475", "860", "959"]},
    "DE": {"name": "Delaware", "area_codes": ["302"]},
    "DC": {"name": "District of Columbia", "area_codes": ["202", "771"]},
    "FL": {"name": "Florida", "area_codes": ["239", "305", "321", "352", "386", "407", "448", "561", "656", "689", "727", "754", "772", "786", "813", "850", "863", "904", "941", "954"]},
    "GA": {"name": "Georgia", "area_codes": ["229", "404", "470", "478", "678", "706", "762", "770", "912", "943"]},
    "HI": {"name": "Hawaii", "area_codes": ["808"]},
    "ID": {"name": "Idaho", "area_codes": ["208", "986"]},
    "IL": {"name": "Illinois", "area_codes": ["217", "224", "309", "312", "331", "447", "464", "618", "630", "708", "730", "773", "779", "815", "847", "872"]},
    "IN": {"name": "Indiana", "area_codes": ["219", "260", "317", "463", "574", "765", "812", "930"]},
    "IA": {"name": "Iowa", "area_codes": ["319", "515", "563", "641", "712"]},
    "KS": {"name": "Kansas", "area_codes": ["316", "620", "785", "913"]},
    "KY": {"name": "Kentucky", "area_codes": ["270", "364", "502", "606", "859"]},
    "LA": {"name": "Louisiana", "area_codes": ["225", "318", "337", "457", "504", "985"]},
    "ME": {"name": "Maine", "area_codes": ["207"]},
    "MD": {"name": "Maryland", "area_codes": ["227", "240", "301", "410", "443", "667"]},
    "MA": {"name": "Massachusetts", "area_codes": ["339", "351", "413", "508", "617", "774", "781", "857", "978"]},
    "MI": {"name": "Michigan", "area_codes": ["231", "248", "269", "313", "517", "586", "616", "679", "734", "810", "906", "947", "989"]},
    "MN": {"name": "Minnesota", "area_codes": ["218", "320", "507", "612", "651", "763", "924", "952"]},
    "MS": {"name": "Mississippi", "area_codes": ["228", "471", "601", "662", "769"]},
    "MO": {"name": "Missouri", "area_codes": ["314", "417", "557", "573", "636", "660", "816", "975"]},
    "MT": {"name": "Montana", "area_codes": ["406"]},
    "NE": {"name": "Nebraska", "area_codes": ["308", "402", "531"]},
    "NV": {"name": "Nevada", "area_codes": ["702", "725", "775"]},
    "NH": {"name": "New Hampshire", "area_codes": ["603"]},
    "NJ": {"name": "New Jersey", "area_codes": ["201", "551", "609", "640", "732", "848", "856", "862", "908", "973", "977"]},
    "NM": {"name": "New Mexico", "area_codes": ["505", "575"]},
    "NY": {"name": "New York", "area_codes": ["212", "315", "329", "332", "347", "363", "516", "518", "585", "607", "624", "631", "646", "680", "716", "718", "838", "845", "914", "917", "929", "934"]},
    "NC": {"name": "North Carolina", "area_codes": ["252", "336", "472", "704", "743", "828", "910", "919", "980", "984"]},
    "ND": {"name": "North Dakota", "area_codes": ["701"]},
    "OH": {"name": "Ohio", "area_codes": ["216", "220", "234", "283", "326", "330", "380", "419", "440", "513", "567", "614", "740", "937"]},
    "OK": {"name": "Oklahoma", "area_codes": ["405", "539", "572", "580", "918"]},
    "OR": {"name": "Oregon", "area_codes": ["458", "503", "541", "971"]},
    "PA": {"name": "Pennsylvania", "area_codes": ["215", "223", "267", "272", "412", "445", "484", "570", "582", "610", "717", "724", "814", "835", "878"]},
    "RI": {"name": "Rhode Island", "area_codes": ["401"]},
    "SC": {"name": "South Carolina", "area_codes": ["803", "839", "843", "854", "864"]},
    "SD": {"name": "South Dakota", "area_codes": ["605"]},
    "TN": {"name": "Tennessee", "area_codes": ["423", "615", "629", "731", "865", "901", "931"]},
    "TX": {"name": "Texas", "area_codes": ["210", "214", "254", "281", "325", "346", "361", "409", "430", "432", "469", "512", "682", "713", "726", "737", "806", "817", "830", "832", "835", "936", "940", "945", "956", "972", "979"]},
    "UT": {"name": "Utah", "area_codes": ["385", "435", "801"]},
    "VT": {"name": "Vermont", "area_codes": ["802"]},
    "VA": {"name": "Virginia", "area_codes": ["276", "434", "540", "571", "703", "757", "804", "826", "948"]},
    "WA": {"name": "Washington", "area_codes": ["206", "253", "360", "425", "509", "564"]},
    "WV": {"name": "West Virginia", "area_codes": ["304", "681"]},
    "WI": {"name": "Wisconsin", "area_codes": ["262", "274", "353", "414", "534", "608", "715", "920"]},
    "WY": {"name": "Wyoming", "area_codes": ["307"]},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def connection() -> sqlite3.Connection:
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    with connection() as db:
        db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                is_pro INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lookups (
                number TEXT PRIMARY KEY,
                carrier TEXT NOT NULL,
                spam TEXT NOT NULL,
                business TEXT NOT NULL,
                directories TEXT NOT NULL,
                public_records TEXT NOT NULL,
                region TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                lookup_count INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS saved_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                number TEXT NOT NULL,
                carrier TEXT,
                line_type TEXT,
                region TEXT,
                business_name TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_email, number)
            );
            CREATE TABLE IF NOT EXISTS saved_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                pattern TEXT NOT NULL,
                area_code TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_email, pattern)
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                user_email TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_used TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email);
            """
        )
        # --- tier column migration (replaces boolean is_pro) ---
        user_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "tier" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'")
            # migrate legacy is_pro flag -> tier
            db.execute("UPDATE users SET tier = 'pro' WHERE is_pro = 1")
            db.execute("UPDATE users SET tier = 'free' WHERE is_pro = 0 OR tier IS NULL OR tier = ''")
        if "password_setup_required" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN password_setup_required INTEGER NOT NULL DEFAULT 0")
        saved_number_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(saved_numbers)").fetchall()
        }
        for name in ("carrier", "line_type", "region", "business_name"):
            if name not in saved_number_columns:
                db.execute(f"ALTER TABLE saved_numbers ADD COLUMN {name} TEXT")
        saved_pattern_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(saved_patterns)").fetchall()
        }
        if "area_code" not in saved_pattern_columns:
            db.execute("ALTER TABLE saved_patterns ADD COLUMN area_code TEXT")
        # --- development seed account: NEVER in production ---
        # The seed Pro account is only created when ALLOW_DEV_SEED=1. On
        # production the account is disabled if it somehow exists (demoted to
        # free with a randomized password) so a published credential can never
        # grant Pro access.
        if os.environ.get("ALLOW_DEV_SEED") == "1":
            if db.execute(
                "SELECT 1 FROM users WHERE email = ?", ("ronald@example.com",)
            ).fetchone() is None:
                db.execute(
                    """
                    INSERT INTO users (email, password, is_pro, tier, created_at)
                    VALUES (?, ?, 1, 'pro', ?)
                    """,
                    ("ronald@example.com", hash_password("password123"), utc_now()),
                )
        else:
            seed_row = db.execute(
                "SELECT tier FROM users WHERE email = ?", ("ronald@example.com",)
            ).fetchone()
            if seed_row is not None and (seed_row["tier"] or "") != TIER_FREE:
                db.execute(
                    "UPDATE users SET tier = ?, is_pro = 0, password = ?, "
                    "password_setup_required = 0 WHERE email = ?",
                    (TIER_FREE, hash_password(secrets.token_hex(32)), "ronald@example.com"),
                )
                db.execute(
                    "DELETE FROM sessions WHERE email = ?", ("ronald@example.com",)
                )
                print(
                    "[security] disabled development seed account "
                    "ronald@example.com (demoted to free, password randomized)"
                )


init_db()


# =====================================================================
# PRO SESSION TOKENS
# =====================================================================
# Pro/Pro+ endpoints authenticate with a Bearer session token issued at
# /api/pro/login. The token is a random secret; only its SHA-256 hash is
# stored. Identity is always derived server-side from the token — a
# client-supplied email address is never trusted for authorization.

SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(30 * 24 * 3600)))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now.timestamp() + SESSION_TTL_SECONDS
    with connection() as db:
        db.execute(
            "INSERT INTO sessions (token_hash, email, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (
                _hash_token(token),
                email.strip().lower(),
                now.isoformat(timespec="seconds"),
                datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return token


def destroy_session(token: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def _session_email(request: Request) -> str:
    """Validate the Bearer session token; return the account email.

    Raises 401 for missing/invalid/expired tokens. Tier is re-read from the
    users table on every call so downgrades take effect immediately.
    """
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing session token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing session token.")
    with connection() as db:
        row = db.execute(
            "SELECT email, expires_at FROM sessions WHERE token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    try:
        expired = datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        expired = True
    if expired:
        destroy_session(token)
        raise HTTPException(status_code=401, detail="Session expired.")
    return row["email"]


def current_pro_user(request: Request) -> tuple[str, str]:
    """Dependency: authenticated Pro/Pro+ user. Returns (email, tier)."""
    email = _session_email(request)
    tier = get_user_tier(email)
    if tier not in (TIER_PRO, TIER_PROPLUS):
        raise HTTPException(status_code=403, detail="Pro access required.")
    return email, tier


def current_proplus_user(request: Request) -> tuple[str, str]:
    """Dependency: authenticated Pro+ user. Returns (email, tier)."""
    email = _session_email(request)
    tier = get_user_tier(email)
    if tier != TIER_PROPLUS:
        raise HTTPException(status_code=403, detail="Pro+ access required.")
    return email, tier


class ProLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SaveNumberRequest(BaseModel):
    number: str = Field(min_length=1, max_length=40)


class AutoSaveRequest(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    carrier: str = Field(default="", max_length=120)
    line_type: str = Field(default="", max_length=40)
    region: str = Field(default="", max_length=120)
    business_name: str = Field(default="", max_length=160)


class SavePatternRequest(BaseModel):
    pattern: str = Field(min_length=1, max_length=120)
    area_code: str = Field(default="", max_length=8)


class AdminUserRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    email: str = Field(min_length=3, max_length=254)
    user_password: str = Field(min_length=8, max_length=256)
    is_pro: bool = True
    tier: str = Field(default="pro", max_length=16)


class BulkIngestionRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    numbers: str = Field(min_length=1, max_length=100000)


class CheckoutRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    tier: str = Field(min_length=2, max_length=16)  # "pro" | "pro_plus"

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        # Reject malformed emails before any Stripe call is made. New
        # customers check out with just an email (no account required), so
        # the address must be well-formed for fulfillment to reach them.
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class BulkLookupRequest(BaseModel):
    numbers: str = Field(min_length=1, max_length=100000)


class FraudNetworkRequest(BaseModel):
    numbers: str = Field(min_length=1, max_length=100000)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(default="", max_length=80)


# --- rate limiting (SEC-01) ---
# Simple in-memory sliding-window rate limiter for auth endpoints.
# Protects against credential-stuffing / brute-force on login + set_password.
_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
_RATE_LIMIT_MAX_ATTEMPTS = int(os.environ.get("RATE_LIMIT_MAX_ATTEMPTS", "10"))
_rate_limit_hits: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = threading.Lock()


def _rate_limit_key(request: Request) -> str:
    """Identify the client for rate limiting (proxy-aware)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _check_rate_limit(
    request: Request,
    endpoint: str,
    max_attempts: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """Raise 429 if the client exceeded the rate limit for the given endpoint.

    max_attempts / window_seconds default to the global auth-limiter env values
    when not provided, so existing call sites keep their behavior.
    """
    limit = max_attempts if max_attempts is not None else _RATE_LIMIT_MAX_ATTEMPTS
    window = window_seconds if window_seconds is not None else _RATE_LIMIT_WINDOW_SECONDS
    key = f"{endpoint}:{_rate_limit_key(request)}"
    now = time.monotonic()
    cutoff = now - window
    with _rate_limit_lock:
        hits = _rate_limit_hits[key]
        # drop expired entries
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please try again later.",
            )
        hits.append(now)


# --- production hardening (SEC-02, SEC-03) ---
# In production, disable the auto-generated API docs (they expose the full
# route schema including admin endpoints) and restrict CORS to the app's own
# origin instead of a wildcard.
_PRODUCTION = os.environ.get("RENDER") == "true" or os.environ.get("ENVIRONMENT") == "production"
_APP_ORIGIN = os.environ.get("APP_BASE_URL", "https://digitscoper.onrender.com").rstrip("/")

app = FastAPI(
    title="Digitscoper Desktop Engine",
    description="Unified phone lookup, Pro saves, and local SQLite administration.",
    version="2.0.0",
    docs_url=None if _PRODUCTION else "/docs",
    redoc_url=None if _PRODUCTION else "/redoc",
    openapi_url=None if _PRODUCTION else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_APP_ORIGIN] if _PRODUCTION else ["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Password"],
    allow_credentials=False,
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Add baseline security headers to every response.

    Note: X-Frame-Options is intentionally NOT set to DENY/SAMEORIGIN because
    the Capacitor native shell embeds this dashboard in an iframe; framing
    protection would break the mobile app. API responses are JSON and the
    dashboard escapes all user content.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # SEC-04: HSTS — the app is HTTPS-only (Render + Cloudflare enforce TLS).
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def normalize_number(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    if digits.startswith("00"):
        digits = digits[2:]
    if not 7 <= len(digits) <= 15:
        raise HTTPException(
            status_code=400,
            detail="Enter a valid phone number with 7 to 15 digits.",
        )
    return f"+{digits}"


BULK_NUMBER_PATTERN = re.compile(r"^\s*\+?\d[\d\s().-]{5,23}\d\s*$")


def parse_bulk_targets(raw_numbers: str) -> tuple[list[str], list[dict[str, str]]]:
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_entry in enumerate(re.split(r"[\r\n,;]+", raw_numbers), start=1):
        entry = raw_entry.strip()
        if not entry:
            continue
        digits = re.sub(r"\D", "", entry)
        if not BULK_NUMBER_PATTERN.fullmatch(entry) or not 7 <= len(digits) <= 15:
            rejected.append({"line": str(line_number), "value": entry, "reason": "Invalid phone number format"})
            continue
        normalized = f"+{digits[2:] if digits.startswith('00') else digits}"
        if normalized in seen:
            rejected.append({"line": str(line_number), "value": entry, "reason": "Duplicate in batch"})
            continue
        seen.add(normalized)
        accepted.append(normalized)
    return accepted, rejected


def carrier_metadata(number: str) -> dict[str, Any]:
    """Legacy local metadata retained only for old database records."""
    digest = hashlib.sha256(number.encode("utf-8")).digest()
    carriers = [
        ("Northstar Wireless", "mobile", "wireless"),
        ("Civic Fiber", "fixed", "landline"),
        ("Atlas Mobile", "mobile", "wireless"),
        ("Waypoint Telecom", "fixed/mobile", "voip"),
    ]
    regions = [
        ("Pacific Northwest", "America/Los_Angeles"),
        ("Mountain West", "America/Denver"),
        ("Central States", "America/Chicago"),
        ("Eastern Seaboard", "America/New_York"),
    ]
    carrier = carriers[digest[0] % len(carriers)]
    region = regions[digest[1] % len(regions)]
    digits = re.sub(r"\D", "", number)
    if any(signature in digits for signature in ("555", "888", "999")):
        carrier = ("Google Voice", "voip", "voip")
    elif any(signature in digits for signature in ("40822", "40897")):
        carrier = ("Civic Fiber", "fixed", "landline")
    return {
        "name": carrier[0],
        "type": carrier[1],
        "line_type": carrier[2],
        "region": region[0],
        "timezone": region[1],
        "source": "Digitscoper local signal index",
    }


def fetch_ipqs_record(number: str) -> dict[str, Any]:
    """Fetch live phone intelligence from IPQualityScore without exposing the API key."""
    api_key = os.environ.get("IPQUALITYSCORE_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Live phone intelligence is not configured. Add IPQUALITYSCORE_API_KEY.",
        )
    endpoint = (
        f"{IPQS_ENDPOINT}/{urllib.parse.quote(api_key, safe='')}/"
        f"{urllib.parse.quote(number, safe='')}"
    )
    request = urllib.request.Request(
        endpoint,
        headers={"User-Agent": "Digitscoper/2.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=502,
            detail=f"Live phone intelligence request failed: {error}",
        ) from error
    if not payload.get("success"):
        raise HTTPException(
            status_code=502,
            detail=payload.get("message") or "The live phone intelligence provider rejected the lookup.",
        )
    return payload


def live_record(number: str, payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Normalize IPQS fields into the response shape used by the dashboard."""
    fraud_score = int(payload.get("fraud_score") or 0)
    active = payload.get("active")
    active_status = payload.get("active_status") or (
        "Active line" if active is True else "Inactive or disconnected" if active is False else "Unknown"
    )
    recent_abuse = bool(payload.get("recent_abuse"))
    spammer = bool(payload.get("spammer") or payload.get("spam_number"))
    if spammer or recent_abuse or fraud_score >= 90:
        risk_label = "High risk"
    elif fraud_score >= 75 or payload.get("risky"):
        risk_label = "Suspicious"
    else:
        risk_label = "Lower risk"

    carrier_name = payload.get("carrier") or "Unknown carrier"
    line_type = payload.get("line_type") or "Unknown line type"
    caller_name = payload.get("name")
    if caller_name in (None, "", "N/A", "Unknown"):
        caller_name = None
    is_commercial = bool(payload.get("is_commercial"))
    business_name = caller_name if is_commercial and caller_name else None
    carrier = {
        "name": carrier_name,
        "type": line_type,
        "line_type": line_type,
        "region": payload.get("region") or "",
        "timezone": payload.get("timezone") or "",
        "source": "IPQualityScore Phone Validation API",
        "active": active,
        "active_status": active_status,
    }
    spam = {
        "score": fraud_score,
        "label": risk_label,
        "reports": None,
        "recent_abuse": recent_abuse,
        "spammer": spammer,
    }
    business = {
        "listed": bool(business_name),
        "name": business_name,
        "address": None,
        "category": "Commercial entity" if is_commercial else None,
        "website": None,
        "hours": None,
    }
    directories = {
        "caller_name": bool(caller_name),
        "commercial_listing": is_commercial,
    }
    public_records = {
        "provider_leaked": bool(payload.get("leaked")),
        "reported_spammer": spammer,
        "do_not_call": bool(payload.get("do_not_call")),
    }
    region = {
        "city": payload.get("city"),
        "state": payload.get("region"),
        "country": payload.get("country"),
        "zip_code": payload.get("zip_code"),
        "timezone": payload.get("timezone"),
    }
    return {
        "number": payload.get("formatted") or number,
        "carrier": carrier,
        "line_status": {
            "active": active,
            "label": active_status,
        },
        "spam": spam,
        "business": business,
        "directories": directories,
        "public_records": public_records,
        "region": region,
        "provider": {
            "name": "IPQualityScore",
            "request_id": payload.get("request_id"),
        },
        "first_seen": now,
        "last_seen": now,
    }


# PERF-01: IPQS lookup cache — serve the DB-cached record when it is fresher
# than the TTL instead of making another billable IPQualityScore API call.
_LOOKUP_CACHE_TTL_SECONDS = int(os.environ.get("LOOKUP_CACHE_TTL_SECONDS", "86400"))

# PRIV-05: data retention — lookup-ledger rows that have not been refreshed
# within this many days are purged automatically on startup. The shared
# ledger expires on its own instead of accumulating third-party PII forever.
# Set to 0 to disable automatic expiry.
LOOKUP_RETENTION_DAYS = int(os.environ.get("LOOKUP_RETENTION_DAYS", "90"))


def prune_expired_lookups() -> int:
    """Delete lookup rows whose last_seen is older than the retention window.

    Returns the number of rows removed. Rows with unparsable timestamps are
    left alone (never delete what we cannot age).
    """
    if LOOKUP_RETENTION_DAYS <= 0:
        return 0
    with connection() as db:
        cursor = db.execute(
            "DELETE FROM lookups "
            "WHERE datetime(REPLACE(last_seen, 'T', ' ')) "
            "< datetime('now', '-' || ? || ' days')",
            (LOOKUP_RETENTION_DAYS,),
        )
        return cursor.rowcount or 0


_pruned_lookup_rows = prune_expired_lookups()
if _pruned_lookup_rows:
    print(
        f"[privacy] pruned {_pruned_lookup_rows} expired lookup row(s) "
        f"(retention {LOOKUP_RETENTION_DAYS}d)"
    )

# PERF-02: rate limit on the free /api/lookup endpoint so one client cannot
# burn through the owner's IPQualityScore quota. 30 requests/minute per IP
# by default (env-overridable).
_LOOKUP_RATE_LIMIT_MAX = int(os.environ.get("LOOKUP_RATE_LIMIT_MAX", "30"))
_LOOKUP_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("LOOKUP_RATE_LIMIT_WINDOW_SECONDS", "60")
)


def _parse_utc(ts: Any) -> datetime | None:
    """Parse a stored UTC timestamp; return None when it cannot be parsed."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _cached_lookup_record(row: sqlite3.Row, now: str) -> dict[str, Any]:
    """Rebuild the lookup response shape from a persisted DB row (cache hit)."""
    carrier = _safe_json(row["carrier"])
    line_status = {
        "active": carrier.get("active"),
        "label": carrier.get("active_status") or "Unknown",
    }
    return {
        "number": row["number"],
        "carrier": carrier,
        "line_status": line_status,
        "spam": _safe_json(row["spam"]),
        "business": _safe_json(row["business"]),
        "directories": _safe_json(row["directories"]),
        "public_records": _safe_json(row["public_records"]),
        "region": _safe_json(row["region"]),
        "provider": {"name": "IPQualityScore", "request_id": None},
        "first_seen": row["first_seen"],
        "last_seen": now,
        "lookup_count": row["lookup_count"],
        "cached": True,
    }


def lookup_record(number: str) -> dict[str, Any]:
    normalized = normalize_number(number)
    now = utc_now()
    with connection() as db:
        row = db.execute(
            "SELECT * FROM lookups WHERE number = ?", (normalized,)
        ).fetchone()
        if row:
            # PERF-01: serve the cached record when it is fresher than the TTL —
            # no billable IPQS call, no hard dependency on provider availability.
            last_seen = _parse_utc(row["last_seen"])
            if last_seen is not None:
                age = (datetime.now(timezone.utc) - last_seen).total_seconds()
                if 0 <= age < _LOOKUP_CACHE_TTL_SECONDS:
                    # Atomic increment (also fixes PERF-07 on this path).
                    db.execute(
                        "UPDATE lookups SET last_seen = ?, lookup_count = lookup_count + 1 "
                        "WHERE number = ?",
                        (now, normalized),
                    )
                    fresh = db.execute(
                        "SELECT * FROM lookups WHERE number = ?", (normalized,)
                    ).fetchone()
                    return _cached_lookup_record(fresh, now)
        # Cache miss / stale / unparsable timestamp: fetch live and upsert.
        live = live_record(normalized, fetch_ipqs_record(normalized), now)
        if row:
            db.execute(
                """
                UPDATE lookups SET carrier = ?, spam = ?, business = ?,
                    directories = ?, public_records = ?, region = ?,
                    last_seen = ?, lookup_count = lookup_count + 1
                WHERE number = ?
                """,
                (
                    json.dumps(live["carrier"]),
                    json.dumps(live["spam"]),
                    json.dumps(live["business"]),
                    json.dumps(live["directories"]),
                    json.dumps(live["public_records"]),
                    json.dumps(live["region"]),
                    now,
                    normalized,
                ),
            )
            fresh = db.execute(
                "SELECT * FROM lookups WHERE number = ?", (normalized,)
            ).fetchone()
            live.update(
                {
                    "first_seen": fresh["first_seen"],
                    "last_seen": now,
                    "lookup_count": fresh["lookup_count"],
                    "cached": False,
                }
            )
            return live

        db.execute(
            """
            INSERT INTO lookups (
                number, carrier, spam, business, directories, public_records,
                region, first_seen, last_seen, lookup_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                normalized,
                json.dumps(live["carrier"]),
                json.dumps(live["spam"]),
                json.dumps(live["business"]),
                json.dumps(live["directories"]),
                json.dumps(live["public_records"]),
                json.dumps(live["region"]),
                now,
                now,
            ),
        )
        live["lookup_count"] = 1
        live["cached"] = False
        return live


def get_user_tier(email: str) -> str:
    """Return the subscription tier for a user, or 'free' if unknown."""
    with connection() as db:
        row = db.execute(
            "SELECT tier, is_pro FROM users WHERE lower(email) = lower(?)", (email,)
        ).fetchone()
    if row is None:
        return TIER_FREE
    tier = (row["tier"] or "").strip().lower()
    if tier in VALID_TIERS:
        return tier
    # legacy fallback
    return TIER_PRO if row["is_pro"] else TIER_FREE


def set_user_tier(email: str, tier: str) -> None:
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier: {tier}")
    with connection() as db:
        db.execute(
            "UPDATE users SET tier = ?, is_pro = ? WHERE lower(email) = lower(?)",
            (tier, 1 if tier in (TIER_PRO, TIER_PROPLUS) else 0, email),
        )


def require_pro(email: str) -> str:
    """Require Pro or Pro+ tier. Returns the user's tier."""
    tier = get_user_tier(email)
    if tier not in (TIER_PRO, TIER_PROPLUS):
        raise HTTPException(status_code=403, detail="Pro access required.")
    return tier


def require_proplus(email: str) -> str:
    """Require Pro+ tier only. Returns the user's tier."""
    tier = get_user_tier(email)
    if tier != TIER_PROPLUS:
        raise HTTPException(status_code=403, detail="Pro+ access required.")
    return tier


def dashboard_data(email: str) -> dict[str, Any]:
    require_pro(email)
    with connection() as db:
        numbers = [
            {
                "number": row["number"],
                "carrier": row["carrier"] or "",
                "line_type": row["line_type"] or "",
                "region": row["region"] or "",
                "business_name": row["business_name"] or "",
            }
            for row in db.execute(
                """
                SELECT number, carrier, line_type, region, business_name
                FROM saved_numbers WHERE user_email = ?
                """
                "ORDER BY created_at DESC",
                (email,),
            ).fetchall()
        ]
        patterns = [
            {"pattern": row["pattern"], "area_code": row["area_code"] or ""}
            for row in db.execute(
                "SELECT pattern, area_code FROM saved_patterns WHERE user_email = ? "
                "ORDER BY created_at DESC",
                (email,),
            ).fetchall()
        ]
    return {
        "saved_numbers": numbers,
        "saved_patterns": patterns,
        "analytics": {
            "total_saved_numbers": len(numbers),
            "total_saved_patterns": len(patterns),
        },
    }


@app.get("/healthz")
@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "digitscoper"}


@app.get("/lookup/{number}")
@app.get("/api/lookup/{number}")
def lookup(number: str, request: Request) -> dict[str, Any]:
    # PERF-02: rate-limit the free lookup endpoint per client IP so one actor
    # cannot burn through the owner's IPQualityScore quota.
    _check_rate_limit(
        request,
        "api_lookup",
        max_attempts=_LOOKUP_RATE_LIMIT_MAX,
        window_seconds=_LOOKUP_RATE_LIMIT_WINDOW_SECONDS,
    )
    return lookup_record(number)


@app.get("/area-codes")
@app.get("/api/area-codes")
def area_codes() -> dict[str, Any]:
    return {
        "states": [
            {"code": code, "name": value["name"], "area_codes": value["area_codes"]}
            for code, value in sorted(STATE_AREA_CODES.items(), key=lambda item: item[1]["name"])
        ]
    }


def _safe_json(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@app.get("/history")
@app.get("/api/history")
def history() -> dict[str, Any]:
    """Return recent lookup history, most recent first. Only shows numbers actually scanned in Digitscoper. No fake records."""
    items: list[dict[str, Any]] = []
    with connection() as db:
        rows = db.execute(
            "SELECT number, carrier, spam, region, first_seen, last_seen, lookup_count "
            "FROM lookups ORDER BY last_seen DESC LIMIT 50"
        ).fetchall()
    for row in rows:
        carrier = _safe_json(row["carrier"])
        spam = _safe_json(row["spam"])
        region = _safe_json(row["region"])
        items.append({
            "number": row["number"],
            "carrier": carrier.get("name") or "Unknown",
            "line_type": carrier.get("line_type") or carrier.get("type") or "",
            "fraud_score": spam.get("score"),
            "risk_label": spam.get("label") or "",
            "city": region.get("city") or "",
            "state": region.get("state") or "",
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "lookup_count": row["lookup_count"],
        })
    return {"items": items}


@app.delete("/history")
@app.delete("/api/history")
def clear_history(request: Request) -> dict[str, Any]:
    """Clear the local lookup history ledger (admin only)."""
    admin_check_request(request)
    with connection() as db:
        result = db.execute("DELETE FROM lookups")
        db.commit()
        return {"cleared": result.rowcount}


@app.get("/number_finder")
@app.get("/api/number_finder")
def number_finder(suffix: str = Query(...), state: str = Query("")) -> dict[str, Any]:
    """Find numbers in the local ledger ending in a 4-digit suffix,
    grouped by state then city. Local SQLite only — no external API calls.
    Only shows numbers actually scanned in Digitscoper. No fake records."""
    if not re.fullmatch(r"\d{4}", suffix or ""):
        raise HTTPException(status_code=400, detail="Suffix must be exactly 4 digits.")
    state_filter = (state or "").upper().strip()
    if state_filter and state_filter not in STATE_AREA_CODES:
        raise HTTPException(status_code=400, detail="Unknown state code.")
    pattern = f"%{suffix}"
    with connection() as db:
        rows = db.execute(
            "SELECT number, carrier, region, last_seen FROM lookups WHERE number LIKE ? ORDER BY last_seen DESC LIMIT 200",
            (pattern,),
        ).fetchall()
    groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
    total = 0
    for row in rows:
        region = _safe_json(row["region"])
        st = (region.get("state") or "").upper()
        if state_filter and st != state_filter:
            continue
        city = region.get("city") or "Unknown"
        carrier = _safe_json(row["carrier"])
        entry = {
            "number": row["number"],
            "carrier": carrier.get("name") or "Unknown",
            "city": city,
            "state": st,
            "last_seen": row["last_seen"],
        }
        groups.setdefault(st or "Unknown", {}).setdefault(city, []).append(entry)
        total += 1
    return {
        "suffix": suffix,
        "state_filter": state_filter or None,
        "total": total,
        "groups": groups,
        "note": "Only shows numbers actually scanned in Digitscoper. No fake records.",
    }


@app.get("/pattern_search/{area_code}/{suffix}")
@app.get("/api/pattern_search/{area_code}/{suffix}")
def pattern_search(area_code: str, suffix: str) -> dict[str, Any]:
    """Find exact suffix matches in the local ledger by area code or state."""
    target = area_code.upper()
    if not re.fullmatch(r"\d{3}", target) and target not in STATE_AREA_CODES:
        raise HTTPException(
            status_code=400,
            detail="Choose a valid 3-digit area code or two-letter state code.",
        )
    if not re.fullmatch(r"\d{4}", suffix):
        raise HTTPException(status_code=400, detail="Suffix must be exactly 4 digits.")
    target_area_codes = (
        [target]
        if target.isdigit()
        else STATE_AREA_CODES[target]["area_codes"]
    )

    with connection() as db:
        rows = db.execute(
            "SELECT number, carrier, spam, business FROM lookups ORDER BY last_seen DESC"
        ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        digits = re.sub(r"\D", "", row["number"])
        national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        if not any(national.startswith(code) for code in target_area_codes) or not national.endswith(suffix):
            continue
        carrier = json.loads(row["carrier"])
        spam = json.loads(row["spam"])
        business = json.loads(row["business"])
        matches.append(
            {
                "number": row["number"],
                "carrier": carrier.get("name", "Unknown"),
                "line_type": carrier.get("line_type", "Unknown"),
                "business": business.get("name") or "No business match",
                "spam_score": spam.get("score"),
                "spam_label": spam.get("label", "Unknown"),
                "active": carrier.get("active"),
                "active_status": carrier.get("active_status", "Unknown"),
            }
        )
    return {
        "scope": target,
        "area_codes": target_area_codes,
        "suffix": suffix,
        "matches": matches,
        "source": "Digitscoper local lookup ledger",
        "note": "Live provider data is available after each exact-number lookup; phone intelligence APIs do not enumerate arbitrary phone ranges.",
    }


@app.post("/pro/login")
@app.post("/api/pro/login")
def pro_login(req: ProLoginRequest, request: Request) -> dict[str, Any]:
    # SEC-01: rate-limit auth attempts to blunt credential stuffing.
    _check_rate_limit(request, "pro_login")
    with connection() as db:
        row = db.execute(
            "SELECT password, is_pro, tier FROM users WHERE lower(email) = lower(?)",
            (req.email,),
        ).fetchone()
    if row is None or not verify_password(req.password, row["password"]):
        raise HTTPException(status_code=403, detail="Invalid login.")
    tier = (row["tier"] or "").strip().lower()
    if tier not in VALID_TIERS:
        tier = TIER_PRO if row["is_pro"] else TIER_FREE
    if tier not in (TIER_PRO, TIER_PROPLUS):
        raise HTTPException(status_code=403, detail="This account does not have Pro access.")
    token = create_session(req.email)
    return {
        "status": "ok",
        "pro": True,
        "tier": tier,
        "email": req.email.strip().lower(),
        "token": token,
    }


class ProLogoutRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)


class DeleteAccountRequest(BaseModel):
    confirm: bool = False


@app.post("/pro/logout")
@app.post("/api/pro/logout")
def pro_logout(request: Request, req: Optional[ProLogoutRequest] = None) -> dict[str, Any]:
    """Revoke the current session token (Authorization header preferred)."""
    authorization = request.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not token and req is not None:
        token = req.token.strip()
    if token:
        destroy_session(token)
    return {"status": "signed_out"}


# =====================================================================
# ACCOUNT DELETION (PRIV-04)
# =====================================================================

@app.delete("/pro/account")
@app.delete("/api/pro/account")
def pro_delete_account(req: DeleteAccountRequest, request: Request) -> dict[str, Any]:
    """Permanently delete the authenticated user's account and personal data.

    Any valid session works (including canceled subscribers demoted to the
    free tier), because deletion is a privacy right, not a Pro feature.

    Deletes: user row, all sessions, saved numbers, saved patterns, API
    keys. Scanned numbers live in the shared lookup ledger (a global cache,
    not per-user data) and are NOT removed here — they expire on their own
    under the retention policy (PRIV-05).
    """
    email = _session_email(request)
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Account deletion requires confirm=true in the request body.",
        )
    with connection() as db:
        deleted = {
            "api_keys": db.execute(
                "DELETE FROM api_keys WHERE lower(user_email) = lower(?)", (email,)
            ).rowcount or 0,
            "saved_patterns": db.execute(
                "DELETE FROM saved_patterns WHERE lower(user_email) = lower(?)", (email,)
            ).rowcount or 0,
            "saved_numbers": db.execute(
                "DELETE FROM saved_numbers WHERE lower(user_email) = lower(?)", (email,)
            ).rowcount or 0,
            "sessions": db.execute(
                "DELETE FROM sessions WHERE lower(email) = lower(?)", (email,)
            ).rowcount or 0,
            "user": db.execute(
                "DELETE FROM users WHERE lower(email) = lower(?)", (email,)
            ).rowcount or 0,
        }
    return {"status": "account_deleted", "email": email, "deleted": deleted}


class ProSetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    session_id: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=256)


@app.post("/pro/set_password")
@app.post("/api/pro/set_password")
def pro_set_password(req: ProSetPasswordRequest, request: Request) -> dict[str, Any]:
    """Let a new Stripe subscriber set their password.

    Proof of ownership is the Stripe checkout session ID from the success
    redirect URL: the session is retrieved from Stripe and must be paid and
    addressed to the same email. Only accounts flagged password_setup_required
    (created by the webhook) may use this flow.
    """
    # SEC-01: rate-limit to blunt enumeration / brute-force.
    _check_rate_limit(request, "pro_set_password")
    if not _stripe_ready():
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    email = req.email.strip().lower()
    with connection() as db:
        row = db.execute(
            "SELECT password_setup_required, tier, is_pro FROM users WHERE lower(email) = lower(?)",
            (email,),
        ).fetchone()
    # SEC-05: return a generic error whether the account exists or not, so
    # attackers cannot enumerate registered emails.
    if row is None or not row["password_setup_required"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid request.",
        )
    try:
        checkout = stripe.checkout.Session.retrieve(req.session_id.strip())  # type: ignore[union-attr]
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Could not verify checkout session: {error}") from error
    session_data = _stripe_object_dict(checkout)
    if session_data.get("payment_status") != "paid":
        raise HTTPException(status_code=402, detail="Checkout session is not paid.")
    metadata = _stripe_object_dict(session_data.get("metadata", {}))
    customer_details = _stripe_object_dict(session_data.get("customer_details", {}))
    session_email = (
        metadata.get("email")
        or session_data.get("customer_email")
        or customer_details.get("email")
        or ""
    ).strip().lower()
    if session_email != email:
        raise HTTPException(status_code=403, detail="Checkout session does not match this email.")
    tier = (row["tier"] or "").strip().lower()
    if tier not in VALID_TIERS:
        tier = TIER_PRO if row["is_pro"] else TIER_FREE
    with connection() as db:
        db.execute(
            "UPDATE users SET password = ?, password_setup_required = 0 "
            "WHERE lower(email) = lower(?)",
            (hash_password(req.new_password), email),
        )
        db.execute("DELETE FROM sessions WHERE email = ?", (email,))
    token = create_session(email)
    return {"status": "password_set", "email": email, "tier": tier, "token": token}


@app.post("/pro/save_number")
@app.post("/api/pro/save_number")
def save_number(req: SaveNumberRequest, request: Request) -> dict[str, Any]:
    email, _tier = current_pro_user(request)
    record = lookup_record(req.number)
    with connection() as db:
        db.execute(
            """
            INSERT INTO saved_numbers (
                user_email, number, carrier, line_type, region, business_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_email, number) DO UPDATE SET
                carrier = excluded.carrier,
                line_type = excluded.line_type,
                region = excluded.region,
                business_name = excluded.business_name
            """,
            (
                email,
                record["number"],
                record["carrier"]["name"],
                record["carrier"]["line_type"],
                record["carrier"]["region"],
                record["business"]["name"] or "",
                utc_now(),
            ),
        )
    return dashboard_data(email)


@app.delete("/pro/delete_number/{number}")
@app.delete("/api/pro/delete_number/{number}")
def delete_number(number: str, request: Request) -> dict[str, Any]:
    """Delete one of the authenticated user's saved numbers."""
    email, _tier = current_pro_user(request)
    normalized = normalize_number(number)
    with connection() as db:
        cursor = db.execute(
            "DELETE FROM saved_numbers WHERE lower(user_email) = lower(?) AND number = ?",
            (email, normalized),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Saved number not found.")
    return {"status": "deleted", **dashboard_data(email)}


@app.post("/pro/auto_save")
@app.post("/api/pro/auto_save")
def auto_save(req: AutoSaveRequest, request: Request) -> dict[str, Any]:
    email, _tier = current_pro_user(request)
    normalized = normalize_number(req.number)
    with connection() as db:
        db.execute(
            """
            INSERT INTO saved_numbers (
                user_email, number, carrier, line_type, region, business_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_email, number) DO UPDATE SET
                carrier = excluded.carrier,
                line_type = excluded.line_type,
                region = excluded.region,
                business_name = excluded.business_name
            """,
            (
                email,
                normalized,
                req.carrier.strip(),
                req.line_type.strip(),
                req.region.strip(),
                req.business_name.strip(),
                utc_now(),
            ),
        )
    return {"status": "synchronized", **dashboard_data(email)}


@app.post("/pro/save_pattern")
@app.post("/api/pro/save_pattern")
def save_pattern(req: SavePatternRequest, request: Request) -> dict[str, Any]:
    email, _tier = current_pro_user(request)
    pattern = req.pattern.strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty.")
    with connection() as db:
        db.execute(
            """
            INSERT INTO saved_patterns (user_email, pattern, area_code, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_email, pattern) DO UPDATE SET area_code = excluded.area_code
            """,
            (email, pattern, req.area_code.strip(), utc_now()),
        )
    return dashboard_data(email)


@app.get("/pro/dashboard")
@app.get("/api/pro/dashboard")
def pro_dashboard(request: Request) -> dict[str, Any]:
    email, _tier = current_pro_user(request)
    return dashboard_data(email)


def admin_check(password: str) -> None:
    if not password or not bcrypt.checkpw(
        password.encode("utf-8"),
        hash_password(ADMIN_PASSWORD).encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Invalid admin password.")


def admin_check_request(request: Request) -> None:
    """Authenticate an admin request via the X-Admin-Password header.

    Credentials are never accepted in URL query strings (they leak into
    access logs, proxies, and browser history).
    """
    admin_check(request.headers.get("x-admin-password", ""))


@app.get("/admin/db")
@app.get("/api/admin/db")
def admin_db(request: Request) -> dict[str, Any]:
    admin_check_request(request)
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM lookups ORDER BY last_seen DESC"
        ).fetchall()
    numbers = [
        {
            "number": row["number"],
            "carrier": json.loads(row["carrier"]),
            "spam": json.loads(row["spam"]),
            "business": json.loads(row["business"]),
            "directories": json.loads(row["directories"]),
            "public_records": json.loads(row["public_records"]),
            "region": json.loads(row["region"]),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "lookup_count": row["lookup_count"],
        }
        for row in rows
    ]
    return {"total_numbers": len(numbers), "numbers": numbers}


@app.post("/admin/bulk_seed")
@app.post("/api/admin/bulk_seed")
def admin_bulk_seed(req: BulkIngestionRequest) -> dict[str, Any]:
    admin_check(req.password)
    accepted, rejected = parse_bulk_targets(req.numbers)
    now = utc_now()
    seeded: list[str] = []
    already_indexed: list[str] = []
    pending_carrier = {
        "name": "Pending live verification",
        "type": "unknown",
        "line_type": "Unknown",
        "region": "",
        "timezone": "",
        "source": "Bulk target ingestion · format validated",
        "active": None,
        "active_status": "Not live-verified",
    }
    pending_spam = {
        "score": None,
        "label": "Not live-verified",
        "reports": None,
        "recent_abuse": None,
        "spammer": None,
    }
    pending_business = {
        "listed": False,
        "name": None,
        "address": None,
        "category": None,
        "website": None,
        "hours": None,
    }
    pending_directories = {"caller_name": False, "commercial_listing": False}
    pending_public_records = {
        "provider_leaked": False,
        "reported_spammer": False,
        "do_not_call": False,
    }
    with connection() as db:
        for number in accepted:
            existing = db.execute(
                "SELECT number FROM lookups WHERE number = ?", (number,)
            ).fetchone()
            if existing:
                already_indexed.append(number)
                continue
            db.execute(
                """
                INSERT INTO lookups (
                    number, carrier, spam, business, directories, public_records,
                    region, first_seen, last_seen, lookup_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    number,
                    json.dumps(pending_carrier),
                    json.dumps(pending_spam),
                    json.dumps(pending_business),
                    json.dumps(pending_directories),
                    json.dumps(pending_public_records),
                    json.dumps({}),
                    now,
                    now,
                ),
            )
            seeded.append(number)
    return {
        "status": "bulk_ingestion_complete",
        "submitted": len([entry for entry in re.split(r"[\r\n,;]+", req.numbers) if entry.strip()]),
        "seeded_count": len(seeded),
        "seeded": seeded,
        "already_indexed_count": len(already_indexed),
        "already_indexed": already_indexed,
        "rejected_count": len(rejected),
        "rejected": rejected,
        "note": "Seeded records are format-validated and remain pending live verification until an exact-number scan is run.",
    }


@app.post("/admin/add_user")
@app.post("/api/admin/add_user")
def admin_add_user(req: AdminUserRequest) -> dict[str, Any]:
    admin_check(req.password)
    email = req.email.strip().lower()
    tier = (req.tier or "").strip().lower()
    if tier not in VALID_TIERS:
        tier = TIER_PRO if req.is_pro else TIER_FREE
    with connection() as db:
        db.execute(
            """
            INSERT INTO users (email, password, is_pro, tier, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                password = excluded.password,
                is_pro = excluded.is_pro,
                tier = excluded.tier
            """,
            (
                email,
                hash_password(req.user_password),
                1 if tier in (TIER_PRO, TIER_PROPLUS) else 0,
                tier,
                utc_now(),
            ),
        )
    return {"status": "user_added", "email": email, "pro": tier in (TIER_PRO, TIER_PROPLUS), "tier": tier}


# =====================================================================
# STRIPE BILLING
# =====================================================================

def _stripe_ready() -> bool:
    return bool(STRIPE_AVAILABLE and STRIPE_SECRET_KEY)


@app.post("/stripe/create-checkout")
@app.post("/api/stripe/create-checkout")
def stripe_create_checkout(req: CheckoutRequest) -> dict[str, Any]:
    """Create a Stripe Checkout session for a Pro or Pro+ subscription."""
    if not _stripe_ready():
        reason = []
        if not STRIPE_AVAILABLE: reason.append("stripe lib missing")
        if not STRIPE_SECRET_KEY: reason.append("STRIPE_SECRET_KEY empty")
        raise HTTPException(status_code=503, detail=f"Stripe is not configured ({', '.join(reason)}).")
    tier = req.tier.strip().lower()
    price_id = {TIER_PRO: STRIPE_PRO_PRICE_ID, TIER_PROPLUS: STRIPE_PROPLUS_PRICE_ID}.get(tier)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid tier. Use 'pro' or 'pro_plus'.")
    email = req.email.strip().lower()
    try:
        base_url = os.environ.get("APP_BASE_URL", "https://digitscoper.onrender.com").rstrip("/")
        session = stripe.checkout.Session.create(  # type: ignore[union-attr]
            mode="subscription",
            customer_email=email,
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"email": email, "tier": tier},
            # Copy identity onto the subscription itself so later subscription
            # lifecycle events (e.g. customer.subscription.deleted) can be
            # fulfilled without depending on a Customer.retrieve API call.
            subscription_data={"metadata": {"email": email, "tier": tier}},
            success_url=os.environ.get("STRIPE_SUCCESS_URL", base_url + "/")
            + "?checkout=success&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=os.environ.get("STRIPE_CANCEL_URL", base_url + "/") + "?checkout=cancelled",
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Stripe checkout failed: {error}") from error
    return {"url": session.url, "session_id": session.id}


def _stripe_object_dict(value: Any) -> dict[str, Any]:
    """Convert Stripe SDK resources to ordinary mappings before using dict APIs."""
    if isinstance(value, dict):
        return value
    converter = getattr(value, "to_dict_recursive", None)
    if callable(converter):
        converted = converter()
    else:
        converter = getattr(value, "to_dict", None)
        converted = converter() if callable(converter) else {}
    return converted if isinstance(converted, dict) else {}


@app.post("/stripe/webhook")
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Verify the Stripe signature and fulfill subscription events."""
    if not _stripe_ready():
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured.")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)  # type: ignore[union-attr]
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {error}") from error

    event_payload = _stripe_object_dict(event)
    event_type = event_payload.get("type", "")
    event_data = _stripe_object_dict(event_payload.get("data", {}))
    data_object = _stripe_object_dict(event_data.get("object", {}))

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if data_object.get("payment_status") != "paid":
            return {"status": "pending"}
        metadata = _stripe_object_dict(data_object.get("metadata", {}))
        customer_details = _stripe_object_dict(data_object.get("customer_details", {}))
        email = metadata.get("email") or data_object.get("customer_email") or customer_details.get("email") or ""
        tier = (metadata.get("tier") or "").strip().lower()
        if tier not in (TIER_PRO, TIER_PROPLUS):
            tier = TIER_PRO
        email = email.strip().lower()
        if email:
            with connection() as db:
                exists = db.execute(
                    "SELECT 1 FROM users WHERE lower(email) = lower(?)", (email,)
                ).fetchone()
                if exists:
                    set_user_tier(email, tier)
                else:
                    # Create the account on first successful payment. The
                    # password is a random placeholder; the customer sets a
                    # real password via /api/pro/set_password, which verifies
                    # the Stripe checkout session before allowing it.
                    db.execute(
                        "INSERT INTO users (email, password, is_pro, tier, "
                        "password_setup_required, created_at) "
                        "VALUES (?, ?, ?, ?, 1, ?)",
                        (email, hash_password(secrets.token_hex(16)), 1, tier, utc_now()),
                    )
        return {"status": "fulfilled", "email": email, "tier": tier}

    if event_type == "customer.subscription.deleted":
        # Downgrade: the subscription carries our email in its metadata
        # (set at checkout creation), so no extra API call is needed in the
        # common case. Customer.retrieve is only a fallback, and its
        # failures are logged — never silently swallowed.
        metadata = _stripe_object_dict(data_object.get("metadata", {}))
        email = (metadata.get("email") or "").strip().lower()
        if not email:
            customer_ref = data_object.get("customer")
            customer_id = (
                _stripe_object_dict(customer_ref).get("id")
                if isinstance(customer_ref, dict)
                else customer_ref
            )
            if customer_id:
                try:
                    customer = stripe.Customer.retrieve(customer_id)  # type: ignore[union-attr]
                    email = (_stripe_object_dict(customer).get("email") or "").strip().lower()
                except Exception as error:
                    print(
                        f"[stripe] customer.subscription.deleted: "
                        f"Customer.retrieve failed for {customer_id}: {error}",
                        file=sys.stderr,
                    )
                    email = ""
        if email:
            set_user_tier(email, TIER_FREE)
        else:
            print(
                "[stripe] customer.subscription.deleted: could not determine "
                "customer email; downgrade skipped",
                file=sys.stderr,
            )
        return {"status": "downgraded", "email": email}

    return {"status": "ignored", "type": event_type}


# =====================================================================
# PRO EXPORTS (CSV / PDF)
# =====================================================================

def _pro_saved_numbers(email: str) -> list[dict[str, Any]]:
    require_pro(email)
    with connection() as db:
        rows = db.execute(
            """
            SELECT number, carrier, line_type, region, business_name, created_at
            FROM saved_numbers WHERE lower(user_email) = lower(?)
            ORDER BY created_at DESC
            """,
            (email,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/pro/export/csv")
@app.get("/api/pro/export/csv")
def pro_export_csv(request: Request) -> Response:
    """Download the Pro user's saved numbers as CSV."""
    email, _tier = current_pro_user(request)
    rows = _pro_saved_numbers(email)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["number", "carrier", "line_type", "region", "business_name", "created_at"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="digitscoper-export.csv"'},
    )


def _render_pdf(rows: list[dict[str, Any]], email: str, tier: str) -> bytes:
    """Render a PDF evidence report. Uses reportlab when available,
    otherwise falls back to a minimal hand-built PDF."""
    title = "Digitscoper Intelligence Report"
    lines = [
        title,
        f"Generated: {utc_now()}",
        f"Account: {email} ({tier})",
        f"Saved numbers: {len(rows)}",
        "",
    ]
    for row in rows:
        lines.append(
            f"{row.get('number','')} | {row.get('carrier','')} | "
            f"{row.get('line_type','')} | {row.get('region','')} | "
            f"{row.get('business_name','')}"
        )
    if REPORTLAB_AVAILABLE:
        buffer = io.BytesIO()
        doc = pdf_canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        doc.setTitle(title)
        y = height - 60
        doc.setFont("Helvetica-Bold", 16)
        doc.drawString(50, y, title)
        y -= 24
        doc.setFont("Helvetica", 10)
        for line in lines[1:]:
            if y < 60:
                doc.showPage()
                y = height - 60
                doc.setFont("Helvetica", 10)
            for chunk in [line[i:i + 100] for i in range(0, len(line), 100)] or [""]:
                doc.drawString(50, y, chunk)
                y -= 14
        doc.save()
        return buffer.getvalue()
    # --- minimal PDF fallback (valid single-page PDF) ---
    text_ops = []
    y = 750
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        text_ops.append(f"BT /F1 10 Tf 50 {y} Td ({safe}) Tj ET")
        y -= 14
    content = "\n".join(text_ops).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    ).encode()
    return pdf


@app.get("/pro/export/pdf")
@app.get("/api/pro/export/pdf")
def pro_export_pdf(request: Request) -> Response:
    """Download the Pro user's saved numbers as a PDF report."""
    email, tier = current_pro_user(request)
    rows = _pro_saved_numbers(email)
    pdf_bytes = _render_pdf(rows, email, tier)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="digitscoper-report.pdf"'},
    )


# =====================================================================
# PRO BULK LOOKUP
# =====================================================================

@app.post("/pro/bulk_lookup")
@app.post("/api/pro/bulk_lookup")
def pro_bulk_lookup(req: BulkLookupRequest, request: Request) -> dict[str, Any]:
    """Run live lookups across a batch of numbers. Pro: 50/batch, Pro+: 200/batch."""
    email, tier = current_pro_user(request)
    limit = 200 if tier == TIER_PROPLUS else 50
    accepted, rejected = parse_bulk_targets(req.numbers)
    batch = accepted[:limit]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for number in batch:
        try:
            record = lookup_record(number)
            results.append(
                {
                    "number": record["number"],
                    "carrier": record["carrier"].get("name"),
                    "line_type": record["carrier"].get("line_type"),
                    "region": record["carrier"].get("region"),
                    "city": record["region"].get("city"),
                    "state": record["region"].get("state"),
                    "country": record["region"].get("country"),
                    "spam_score": record["spam"].get("score"),
                    "spam_label": record["spam"].get("label"),
                    "line_status": record.get("line_status", {}).get("label"),
                    "business": record["business"].get("name"),
                }
            )
        except HTTPException as error:
            errors.append({"number": number, "reason": error.detail})
        except Exception as error:  # noqa: BLE001 - batch must not abort on one failure
            errors.append({"number": number, "reason": str(error)})
    return {
        "status": "bulk_lookup_complete",
        "tier": tier,
        "limit": limit,
        "submitted": len(accepted),
        "processed": len(batch),
        "truncated": len(accepted) > limit,
        "results": results,
        "errors": errors,
        "rejected": rejected,
    }


# =====================================================================
# PRO+ FRAUD NETWORK DETECTION
# =====================================================================

def _number_attributes(record: dict[str, Any]) -> dict[str, str]:
    """Extract the linkable attributes from a lookup record."""
    digits = re.sub(r"\D", "", record.get("number", ""))
    national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    area_code = national[:3] if len(national) >= 3 else ""
    return {
        "carrier": (record.get("carrier") or {}).get("name") or "Unknown",
        "region": (record.get("carrier") or {}).get("region") or "Unknown",
        "state": (record.get("region") or {}).get("state") or "Unknown",
        "risk": (record.get("spam") or {}).get("label") or "Unknown",
        "line_type": (record.get("carrier") or {}).get("line_type") or "Unknown",
        "area_code": area_code,
    }


@app.post("/proplus/fraud_network")
@app.post("/api/proplus/fraud_network")
def proplus_fraud_network(req: FraudNetworkRequest, request: Request) -> dict[str, Any]:
    """Flagship Pro+ feature: detect linked fraud networks across a batch.

    Numbers sharing 2+ attributes (carrier, region/state, risk label,
    line type, area code) are linked; connected components become clusters.
    """
    _email, _tier = current_proplus_user(request)
    accepted, rejected = parse_bulk_targets(req.numbers)
    batch = accepted[:200]

    nodes: list[dict[str, Any]] = []
    attrs: list[dict[str, str]] = []
    for number in batch:
        try:
            record = lookup_record(number)
        except Exception:  # noqa: BLE001 - skip numbers that fail live lookup
            continue
        attributes = _number_attributes(record)
        attrs.append(attributes)
        nodes.append(
            {
                "number": record["number"],
                "carrier": attributes["carrier"],
                "region": attributes["region"],
                "state": attributes["state"],
                "risk": attributes["risk"],
                "risk_score": (record.get("spam") or {}).get("score"),
                "line_type": attributes["line_type"],
                "area_code": attributes["area_code"],
                "business": (record.get("business") or {}).get("name"),
            }
        )

    n = len(nodes)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    links: list[dict[str, Any]] = []
    # Strong signals identify an operation; weak ones only corroborate.
    strong_keys = ("carrier", "state", "risk")
    weak_keys = ("line_type", "region", "area_code")
    for i in range(n):
        for j in range(i + 1, n):
            shared_strong = [
                key for key in strong_keys
                if attrs[i][key] != "Unknown" and attrs[i][key] == attrs[j][key]
            ]
            shared_weak = [
                key for key in weak_keys
                if attrs[i][key] != "Unknown" and attrs[i][key] == attrs[j][key]
            ]
            # Link when: 2+ strong signals match, or 1 strong + 2 weak corroborate.
            if len(shared_strong) >= 2 or (len(shared_strong) >= 1 and len(shared_weak) >= 2):
                shared = shared_strong + shared_weak
                union(i, j)
                links.append(
                    {
                        "from": nodes[i]["number"],
                        "to": nodes[j]["number"],
                        "reason": "Shared " + ", ".join(shared),
                        "shared_traits": shared,
                    }
                )

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters: list[dict[str, Any]] = []
    for cluster_id, (root, members) in enumerate(sorted(groups.items()), start=1):
        if len(members) < 2:
            continue
        # traits shared by every member of the cluster
        all_keys = strong_keys + weak_keys
        common = [
            key for key in all_keys
            if all(attrs[m][key] != "Unknown" for m in members)
            and len({attrs[m][key] for m in members}) == 1
        ]
        clusters.append(
            {
                "id": cluster_id,
                "size": len(members),
                "members": [nodes[m]["number"] for m in members],
                "shared_traits": {key: attrs[members[0]][key] for key in common},
                "threat": "HIGH" if any(
                    nodes[m]["risk"] in ("High risk", "Suspicious") for m in members
                ) else "WATCH",
            }
        )

    # sort clusters by size, largest first
    clusters.sort(key=lambda c: c["size"], reverse=True)

    return {
        "status": "fraud_network_complete",
        "analyzed": n,
        "submitted": len(accepted),
        "rejected": rejected,
        "nodes": nodes,
        "links": links,
        "clusters": clusters,
        "cluster_count": len(clusters),
        "linked_numbers": sum(c["size"] for c in clusters),
    }


# =====================================================================
# PRO+ API KEYS
# =====================================================================

def _hash_api_key(plain_key: str) -> str:
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def _validate_api_key(plain_key: str) -> Optional[dict[str, Any]]:
    """Validate a bearer API key. Returns {user_email, tier} or None."""
    key_hash = _hash_api_key(plain_key)
    with connection() as db:
        row = db.execute(
            "SELECT user_email, id FROM api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE api_keys SET last_used = ? WHERE key_hash = ?", (utc_now(), key_hash)
        )
    tier = get_user_tier(row["user_email"])
    if tier != TIER_PROPLUS:
        return None
    return {"user_email": row["user_email"], "tier": tier}


async def _bearer_user(request: Request) -> dict[str, Any]:
    """Extract and validate the Bearer API key for /api/v1/ routes."""
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer API key.")
    plain_key = authorization[7:].strip()
    user = _validate_api_key(plain_key)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
    return user


@app.post("/proplus/api_keys")
@app.post("/api/proplus/api_keys")
def proplus_create_api_key(req: ApiKeyCreateRequest, request: Request) -> dict[str, Any]:
    """Generate a new API key. The plain key is returned ONCE."""
    email, _tier = current_proplus_user(request)
    plain_key = "dsk_live_" + secrets.token_urlsafe(32)
    key_hash = _hash_api_key(plain_key)
    name = req.name.strip() or f"Key {utc_now()[:10]}"
    with connection() as db:
        cursor = db.execute(
            """
            INSERT INTO api_keys (key_hash, key_prefix, user_email, name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key_hash, plain_key[:12] + "...", email, name, utc_now()),
        )
        key_id = cursor.lastrowid
    return {
        "status": "key_created",
        "id": key_id,
        "key": plain_key,
        "name": name,
        "warning": "Store this key now. It will never be shown again.",
    }


@app.get("/proplus/api_keys")
@app.get("/api/proplus/api_keys")
def proplus_list_api_keys(request: Request) -> dict[str, Any]:
    """List API keys (masked) for a Pro+ user."""
    email, _tier = current_proplus_user(request)
    with connection() as db:
        rows = db.execute(
            """
            SELECT id, key_prefix, name, created_at, last_used
            FROM api_keys WHERE lower(user_email) = lower(?)
            ORDER BY created_at DESC
            """,
            (email,),
        ).fetchall()
    return {"keys": [dict(row) for row in rows]}


@app.delete("/proplus/api_keys/{key_id}")
@app.delete("/api/proplus/api_keys/{key_id}")
def proplus_delete_api_key(key_id: int, request: Request) -> dict[str, Any]:
    """Revoke an API key."""
    email, _tier = current_proplus_user(request)
    with connection() as db:
        cursor = db.execute(
            "DELETE FROM api_keys WHERE id = ? AND lower(user_email) = lower(?)",
            (key_id, email),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="API key not found.")
    return {"status": "key_revoked", "id": key_id}


# =====================================================================
# PUBLIC API v1 (Bearer token auth)
# =====================================================================

@app.get("/api/v1/lookup/{number}")
async def api_v1_lookup(number: str, request: Request) -> dict[str, Any]:
    """API-key authenticated single number lookup."""
    await _bearer_user(request)
    return lookup_record(number)


@app.post("/api/v1/bulk_lookup")
async def api_v1_bulk_lookup(req: BulkLookupRequest, request: Request) -> dict[str, Any]:
    """API-key authenticated bulk lookup (up to 200 per batch)."""
    user = await _bearer_user(request)
    accepted, rejected = parse_bulk_targets(req.numbers)
    results: list[dict[str, Any]] = []
    for number in accepted[:200]:
        try:
            record = lookup_record(number)
            results.append(
                {
                    "number": record["number"],
                    "carrier": record["carrier"].get("name"),
                    "line_type": record["carrier"].get("line_type"),
                    "spam_score": record["spam"].get("score"),
                    "spam_label": record["spam"].get("label"),
                }
            )
        except Exception as error:  # noqa: BLE001
            results.append({"number": number, "error": str(error)})
    return {"user": user["user_email"], "processed": len(results), "results": results, "rejected": rejected}


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Digitscoper — Desktop Engine</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #070b14;
      --panel: rgba(16, 24, 40, .86);
      --panel-soft: rgba(23, 34, 55, .72);
      --line: rgba(148, 163, 184, .16);
      --text: #ecf4ff;
      --muted: #8ea0b8;
      --blue: #5db8ff;
      --cyan: #6ce3da;
      --danger: #ff7d96;
      --shadow: 0 22px 70px rgba(0, 0, 0, .38);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 12% 0%, rgba(61, 132, 200, .23), transparent 35%),
        radial-gradient(circle at 92% 10%, rgba(58, 186, 175, .15), transparent 28%),
        var(--bg);
    }
    button, input { font: inherit; }
    button { cursor: pointer; }
    .shell { min-height: 100vh; display: flex; flex-direction: column; }
    .topbar {
      display: flex; align-items: center; justify-content: space-between; gap: 20px;
      padding: 20px clamp(18px, 4vw, 54px); border-bottom: 1px solid var(--line);
      background: rgba(7, 11, 20, .68); backdrop-filter: blur(18px);
      position: sticky; top: 0; z-index: 5;
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 220px; }
    .brand-mark {
      width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px;
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      color: #05101d; font-weight: 900; box-shadow: 0 0 28px rgba(93, 184, 255, .26);
    }
    .brand-name { font-size: 14px; letter-spacing: .17em; font-weight: 800; }
    .brand-sub { color: var(--muted); font-size: 11px; margin-top: 2px; }
    .tabs { display: flex; gap: 6px; flex-wrap: nowrap; overflow-x: auto; max-width: 100%; -webkit-overflow-scrolling: touch; scrollbar-width: thin; }
    .tab {
      color: var(--muted); background: transparent; border: 1px solid transparent;
      border-radius: 9px; padding: 9px 15px; transition: .2s ease;
      flex-shrink: 0; white-space: nowrap;
    }
    .tab:hover { color: var(--text); background: rgba(255,255,255,.04); }
    .tab.active { color: #06111d; background: var(--blue); border-color: var(--blue); }
    .top-status { color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 8px; }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 12px var(--cyan); }
    .workspace {
      width: min(1400px, 100%); margin: 0 auto; flex: 1; display: grid;
      grid-template-columns: minmax(0, 1fr) 320px; gap: 18px; padding: 28px clamp(18px, 4vw, 54px);
    }
    .panel {
      border: 1px solid var(--line); border-radius: 18px; background: var(--panel);
      box-shadow: var(--shadow); padding: clamp(20px, 3vw, 34px);
    }
    .main-panel { min-height: 620px; }
    .side-panel { padding: 22px; align-self: start; position: sticky; top: 98px; }
    .view { display: none; animation: rise .25s ease both; }
    .view.active { display: block; }
    @keyframes rise { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
    .eyebrow { text-transform: uppercase; letter-spacing: .16em; color: var(--blue); font-size: 10px; font-weight: 800; }
    h1 { margin: 10px 0 8px; font-size: clamp(28px, 4vw, 46px); letter-spacing: -.045em; line-height: 1.02; }
    h2 { margin: 0 0 6px; font-size: 20px; letter-spacing: -.02em; }
    h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .12em; color: var(--muted); margin: 0 0 12px; }
    p { color: var(--muted); line-height: 1.6; margin: 0; }
    .intro { max-width: 630px; margin-bottom: 28px; }
    .lookup-bar { display: flex; gap: 10px; margin: 22px 0 12px; }
    input {
      width: 100%; color: var(--text); background: #0a111e; border: 1px solid var(--line);
      border-radius: 10px; padding: 13px 14px; outline: none; transition: .2s ease;
    }
    input:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(93,184,255,.12); }
    .lookup-bar input { font-size: 16px; }
    .btn {
      border: 0; border-radius: 10px; padding: 12px 17px; font-weight: 750;
      transition: transform .18s ease, filter .18s ease; white-space: nowrap;
    }
    .btn:hover { transform: translateY(-1px); filter: brightness(1.08); }
    .btn-primary { color: #06111d; background: linear-gradient(135deg, var(--blue), var(--cyan)); }
    .btn-muted { color: var(--text); background: #182338; border: 1px solid var(--line); }
    .status { min-height: 20px; color: var(--cyan); font-size: 12px; }
    .status.error { color: var(--danger); }
    .result { display: none; margin-top: 24px; }
    .result-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
    .result-number { font-size: 22px; font-weight: 800; letter-spacing: -.03em; }
    .badge { display: inline-flex; padding: 5px 9px; border-radius: 99px; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; font-weight: 800; }
    .badge-blue { color: var(--blue); background: rgba(93,184,255,.12); }
    .badge-green { color: var(--cyan); background: rgba(108,227,218,.11); }
    .data-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .data-card { background: var(--panel-soft); border: 1px solid var(--line); border-radius: 12px; padding: 15px; }
    .data-card.wide { grid-column: 1 / -1; }
    .data-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 6px; }
    .data-value { font-weight: 700; font-size: 14px; overflow-wrap: anywhere; }
    .signal-list { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 7px; min-width: 0; }
    .signal-list .pill { display: inline-flex; max-width: 100%; white-space: normal; line-height: 1.35; }
    .data-value code { color: #bed0e5; font-size: 11px; font-weight: 500; white-space: pre-wrap; }
    .form-stack { max-width: 500px; display: grid; gap: 11px; margin-top: 24px; }
    .form-stack label { color: var(--muted); font-size: 11px; }
    .form-stack .btn { justify-self: start; }
    .dashboard { margin-top: 28px; }
    .dashboard-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .dashboard-head .btn { padding: 8px 11px; font-size: 11px; }
    .stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 16px 0; }
    .stat { border: 1px solid var(--line); background: var(--panel-soft); border-radius: 12px; padding: 14px; }
    .stat strong { display: block; font-size: 27px; letter-spacing: -.05em; }
    .stat span { color: var(--muted); font-size: 11px; }
    .saved-list { display: flex; flex-wrap: wrap; gap: 7px; margin: 0 0 18px; }
    .pill { color: #c8d8eb; background: #152237; border: 1px solid var(--line); border-radius: 99px; padding: 7px 10px; font-size: 11px; }
    .pill.yes { color: var(--cyan); background: rgba(108,227,218,.1); border-color: rgba(108,227,218,.24); }
    .pill.no { color: var(--muted); background: rgba(148,163,184,.08); }
    .saved-item { width: 100%; border: 1px solid var(--line); background: var(--panel-soft); border-radius: 12px; padding: 12px; }
    .saved-item strong { display: block; font-size: 13px; color: var(--text); }
    .saved-item span { color: var(--muted); font-size: 11px; }
    .pattern-builder { margin: 22px 0 28px; padding: 16px; border: 1px solid var(--line); border-radius: 14px; background: rgba(23, 34, 55, .48); }
    .pattern-controls { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 8px; align-items: end; }
    .pattern-controls label { display: grid; gap: 6px; color: var(--muted); font-size: 11px; }
    .pattern-results { display: grid; gap: 7px; margin-top: 12px; }
    .pattern-option { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 9px 11px; border: 1px solid var(--line); border-radius: 9px; background: #0a111e; }
    .pattern-option strong { color: var(--blue); font-size: 13px; }
    .pattern-option button { padding: 6px 9px; font-size: 10px; }
    .empty { color: var(--muted); font-size: 12px; }
    .side-block { border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 18px; }
    .side-title { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
    .side-title h3 { margin: 0; }
    .session-row { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; padding: 9px 0; }
    .session-row span:first-child { color: var(--muted); }
    .session-row span:last-child { text-align: right; overflow-wrap: anywhere; }
    .hint { color: var(--muted); font-size: 11px; line-height: 1.7; }
    .hint strong { color: #c7d7e9; font-weight: 650; }
    .admin-output { margin-top: 20px; max-height: 280px; overflow: auto; }
    .db-row { display: flex; justify-content: space-between; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
    .db-row span:last-child { color: var(--muted); }
    .bulk-ingestion { margin-top: 26px; border: 1px solid var(--line); border-radius: 14px; background: rgba(23, 34, 55, .48); overflow: hidden; }
    .bulk-ingestion summary { cursor: pointer; padding: 16px; color: var(--text); font-size: 13px; font-weight: 750; }
    .bulk-ingestion summary::marker { color: var(--blue); }
    .bulk-ingestion-body { display: grid; gap: 11px; padding: 0 16px 16px; }
    .bulk-ingestion-body label { color: var(--muted); font-size: 11px; }
    .bulk-ingestion-body textarea { width: 100%; min-height: 150px; resize: vertical; color: var(--text); background: #0a111e; border: 1px solid var(--line); border-radius: 10px; padding: 13px 14px; outline: none; font: inherit; line-height: 1.5; }
    .bulk-ingestion-body textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(93,184,255,.12); }
    .ingestion-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .ingestion-card { border: 1px solid var(--line); background: var(--panel-soft); border-radius: 10px; padding: 11px; }
    .ingestion-card strong { display: block; font-size: 20px; letter-spacing: -.03em; }
    .ingestion-card span { color: var(--muted); font-size: 10px; }
    .ingestion-list { max-height: 150px; overflow: auto; display: grid; gap: 6px; }
    .ingestion-list div { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
    footer { padding: 16px; color: #56667c; text-align: center; font-size: 10px; letter-spacing: .08em; }
    @media (max-width: 820px) {
      .topbar { align-items: flex-start; flex-wrap: wrap; }
      .top-status { margin-left: auto; }
      .workspace { grid-template-columns: 1fr; }
      .side-panel { position: static; }
    }
    @media (max-width: 520px) {
      .tabs { width: 100%; order: 3; }
      .tab { flex: 0 0 auto; }
      .lookup-bar { flex-direction: column; }
      .data-grid { grid-template-columns: 1fr; }
      .data-card.wide { grid-column: auto; }
      .result-head { align-items: flex-start; flex-direction: column; }
      .pattern-controls { grid-template-columns: 1fr; }
      .ingestion-summary { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">D</div>
        <div><div class="brand-name">DIGITSCOPER</div><div class="brand-sub">Desktop intelligence engine</div></div>
      </div>
      <nav class="tabs" aria-label="Primary navigation">
        <button class="tab active" data-view="lookup">Lookup</button>
        <button class="tab" data-view="pro">Pro</button>
        <button class="tab" data-view="bulk">Bulk Lookup</button>
        <button class="tab" data-view="fraud">Fraud Network</button>
        <button class="tab" data-view="pricing">Pricing</button>
        <button class="tab" data-view="admin">Admin</button>
      </nav>
      <div class="top-status"><span class="dot"></span> Local engine online</div>
      <button id="topbar-auth-button" class="btn btn-primary" style="padding: 8px 14px; font-size: 12px; flex-shrink: 0;" title="Sign in to Pro">Sign in</button>
    </header>
    <main class="workspace">
      <section class="panel main-panel">
        <div id="view-lookup" class="view active">
          <div class="eyebrow">Unified phone lookup</div>
          <h1>See the signal<br>behind the number.</h1>
           <p class="intro">Run a live IPQualityScore scan across carrier, line status, business, risk, and reputation signals. Results are cached in your private SQLite engine for the next pass.</p>
          <div class="lookup-bar">
            <input id="lookup-number" type="text" inputmode="tel" placeholder="+1 (415) 555-0198" aria-label="Phone number">
            <button id="lookup-button" class="btn btn-primary">Run scan</button>
          </div>
          <div id="lookup-status" class="status" role="status"></div>
          <div id="lookup-result" class="result">
            <div class="result-head">
              <div><div class="eyebrow">Latest scan</div><div id="result-number" class="result-number">—</div></div>
              <span id="result-count" class="badge badge-blue">1 scan</span>
            </div>
            <div class="data-grid">
              <div class="data-card"><div class="data-label">Carrier</div><div id="carrier-name" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Line type</div><div id="carrier-line" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Region</div><div id="carrier-region" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Timezone</div><div id="carrier-tz" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Risk score</div><div id="spam-score" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Risk label</div><div id="spam-label" class="data-value">—</div></div>
               <div class="data-card"><div class="data-label">Line status</div><div id="line-active" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Business listing</div><div id="biz-listed" class="data-value">—</div></div>
              <div class="data-card"><div class="data-label">Business name</div><div id="biz-name" class="data-value">—</div></div>
               <div class="data-card wide"><div class="data-label">Directory coverage</div><div id="directories" class="data-value signal-list"><code>—</code></div></div>
               <div class="data-card wide"><div class="data-label">Public record signals</div><div id="public-records" class="data-value signal-list"><code>—</code></div></div>
            </div>
          </div>
        </div>
        <div id="view-pro" class="view">
          <div class="eyebrow">Pro workspace</div>
          <h2>Save the patterns worth returning to.</h2>
          <p>Sign in to keep a private watchlist of numbers and search patterns on this device.</p>
          <div id="pro-login-form" class="form-stack">
            <label for="pro-email">Email</label><input id="pro-email" type="email" placeholder="you@example.com">
            <label for="pro-password">Password</label><input id="pro-password" type="password" placeholder="Your password">
            <button id="pro-login-button" class="btn btn-primary">Unlock Pro</button>
          </div>
          <div id="pro-setup-form" class="form-stack" style="display:none">
            <h3>Set your Pro password</h3>
            <p class="hint">Your subscription is active. Choose a password to unlock your Pro workspace.</p>
            <label for="setup-email">Email</label><input id="setup-email" type="email" placeholder="you@example.com">
            <label for="setup-password">New password</label><input id="setup-password" type="password" placeholder="At least 8 characters">
            <button id="pro-setup-button" class="btn btn-primary">Set password &amp; sign in</button>
          </div>
          <div id="pro-status" class="status" role="status"></div>
          <div id="pro-content" class="dashboard" style="display:none">
            <div class="eyebrow">Saved intelligence</div>
            <div class="dashboard-head"><div><h3>Private Pro session</h3><p class="hint">Lookups are saved automatically while signed in.</p></div><button id="pro-signout-button" class="btn btn-muted">Sign out</button></div>
            <div class="pattern-builder">
              <h3>Four-digit pattern builder</h3>
              <p class="hint">Choose a state to search every mapped area code, or narrow to one area code. Results come from numbers already checked in the local ledger; live provider data is collected when you run an exact-number lookup.</p>
              <div class="pattern-controls">
                <label for="pattern-state">State
                  <select id="pattern-state"></select>
                </label>
                <label for="pattern-area-code">Area code scope
                  <select id="pattern-area-code"><option value="ALL">All area codes</option></select>
                </label>
                <label for="pattern-suffix">Final four digits
                  <input id="pattern-suffix" inputmode="numeric" maxlength="4" placeholder="0198">
                </label>
                <button id="pattern-generate-button" class="btn btn-primary">Generate</button>
              </div>
              <div id="pattern-results" class="pattern-results"></div>
            </div>
            <div class="stats"><div class="stat"><strong id="saved-number-count">0</strong><span>saved numbers</span></div><div class="stat"><strong id="saved-pattern-count">0</strong><span>saved patterns</span></div></div>
            <div style="display:flex; gap:8px; margin-bottom:18px; flex-wrap:wrap;">
              <button id="export-csv-button" class="btn btn-muted">⬇ Export CSV</button>
              <button id="export-pdf-button" class="btn btn-muted">⬇ Export PDF Report</button>
              <span id="tier-badge" class="badge badge-blue" style="align-self:center;"></span>
            </div>
            <div id="apikey-section" style="display:none; margin-bottom:18px; padding:16px; border:1px solid var(--line); border-radius:14px; background:rgba(23,34,55,.48);">
              <h3>🔑 API Keys <span class="badge badge-green">Pro+</span></h3>
              <p class="hint">Use keys with the <code>/api/v1/</code> endpoints. The full key is shown once at creation.</p>
              <div class="lookup-bar">
                <input id="apikey-name" placeholder="Key name (e.g. my-script)">
                <button id="apikey-create-button" class="btn btn-primary">Create key</button>
              </div>
              <div id="apikey-new" class="status" style="word-break:break-all;"></div>
              <div id="apikey-list" style="display:grid; gap:7px; margin-top:10px;"></div>
            </div>
            <div class="form-stack">
              <label for="save-number">Save a number</label><div class="lookup-bar"><input id="save-number" placeholder="+1 415 555 0198"><button id="save-number-button" class="btn btn-muted">Save</button></div>
              <label for="save-pattern">Save a pattern</label><div class="lookup-bar"><input id="save-pattern" placeholder="415-555-*"><button id="save-pattern-button" class="btn btn-muted">Save</button></div>
            </div>
            <h3>Numbers</h3><div id="saved-numbers" class="saved-list"></div>
            <h3>Patterns</h3><div id="saved-patterns" class="saved-list"></div>
          </div>
        </div>
        <div id="view-bulk" class="view">
          <div class="eyebrow">Pro · Bulk lookup</div>
          <h2>Scan numbers in batches.</h2>
          <p>Pro: 50 per batch. Pro+: 200 per batch. Paste one number per line.</p>
          <div class="form-stack" style="max-width:100%;">
            <label for="bulk-numbers">Number batch</label>
            <textarea id="bulk-numbers" style="width:100%; min-height:150px; resize:vertical; color:var(--text); background:#0a111e; border:1px solid var(--line); border-radius:10px; padding:13px 14px; font:inherit; line-height:1.5;" placeholder="+1 (415) 555-0198&#10;408-559-9314&#10;..."></textarea>
            <button id="bulk-run-button" class="btn btn-primary">Run bulk scan</button>
          </div>
          <div id="bulk-status" class="status" role="status"></div>
          <div id="bulk-results" style="margin-top:16px; overflow-x:auto;"></div>
        </div>
        <div id="view-fraud" class="view">
          <div class="eyebrow">Pro+ · Fraud network detection</div>
          <h2>Find the operation behind the numbers.</h2>
          <p>Paste scam-call numbers. Digitscoper links numbers sharing carrier, state, risk and line signals — then maps the clusters so you can see the whole operation.</p>
          <div class="form-stack" style="max-width:100%;">
            <label for="fraud-numbers">Suspect number batch</label>
            <textarea id="fraud-numbers" style="width:100%; min-height:150px; resize:vertical; color:var(--text); background:#0a111e; border:1px solid var(--line); border-radius:10px; padding:13px 14px; font:inherit; line-height:1.5;" placeholder="+1 (415) 555-0198&#10;..."></textarea>
            <button id="fraud-run-button" class="btn btn-primary">🕸 Detect fraud network</button>
          </div>
          <div id="fraud-status" class="status" role="status"></div>
          <div id="fraud-summary" style="margin-top:16px;"></div>
          <div id="fraud-graph" style="margin-top:12px;"></div>
          <div id="fraud-clusters" style="margin-top:12px; display:grid; gap:10px;"></div>
        </div>
        <div id="view-pricing" class="view">
          <div class="eyebrow">Plans</div>
          <h2>Pick your firepower.</h2>
          <p>Upgrade with Stripe. Your tier unlocks instantly when payment completes.</p>
          <div id="checkout-email-row" style="margin-top:16px; max-width:420px;">
            <label for="checkout-email" class="hint" style="display:block; margin-bottom:6px;">Email for your Pro account</label>
            <input id="checkout-email" type="email" inputmode="email" autocomplete="email" placeholder="you@example.com"
              style="width:100%; color:var(--text); background:#0a111e; border:1px solid var(--line); border-radius:10px; padding:12px 14px; font:inherit;" />
            <p class="hint" id="checkout-email-hint" style="margin-top:6px;">New here? No signup needed — enter your email, pay, then set a password on the Pro tab.</p>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin-top:20px;">
            <div class="data-card">
              <div class="eyebrow">Free</div>
              <h2 style="margin:8px 0;">$0</h2>
              <p class="hint">Single lookups<br>Local ledger cache<br>Pattern search</p>
              <div style="margin-top:12px;"><span class="badge badge-blue">Current</span></div>
            </div>
            <div class="data-card" style="border-color:rgba(93,184,255,.4);">
              <div class="eyebrow">Pro</div>
              <h2 style="margin:8px 0;">$19<span style="font-size:13px; color:var(--muted);">/mo</span></h2>
              <p class="hint">Everything in Free<br>Saved watchlist &amp; history<br>Bulk lookup (50/batch)<br>CSV + PDF exports</p>
              <button id="checkout-pro-button" class="btn btn-primary" style="margin-top:12px; width:100%;">Upgrade to Pro</button>
            </div>
            <div class="data-card" style="border-color:rgba(108,227,218,.4);">
              <div class="eyebrow">Pro+</div>
              <h2 style="margin:8px 0;">$49<span style="font-size:13px; color:var(--muted);">/mo</span></h2>
              <p class="hint">Everything in Pro<br>Bulk lookup (200/batch)<br>🕸 Fraud network detection<br>🔑 API access</p>
              <button id="checkout-proplus-button" class="btn btn-primary" style="margin-top:12px; width:100%;">Upgrade to Pro+</button>
            </div>
          </div>
          <div id="pricing-status" class="status" role="status" style="margin-top:14px;"></div>
          <p class="hint" style="margin-top:10px;">After checkout you'll land back here to set a password and unlock your Pro workspace. Signed in already? Checkout uses your account email.</p>
        </div>
        <div id="view-admin" class="view">
          <div class="eyebrow">Local administration</div>
          <h2>Inspect the engine ledger.</h2>
          <p>Admin access is protected by a bcrypt-checked password and only exposes local lookup records.</p>
          <div class="form-stack">
            <label for="admin-password">Admin password</label><input id="admin-password" type="password" placeholder="Enter admin password">
            <button id="admin-load-button" class="btn btn-primary">Load lookup database</button>
          </div>
          <div id="admin-status" class="status" role="status"></div>
          <div id="admin-output" class="admin-output"></div>
           <details class="bulk-ingestion">
             <summary>Bulk Target Ingestion Node</summary>
             <div class="bulk-ingestion-body">
               <p class="hint">Paste one raw target number per line. Format validation seeds the local ledger without spending live lookup credits; exact scans can enrich each seeded record later.</p>
               <label for="bulk-targets">Raw target batch</label>
               <textarea id="bulk-targets" placeholder="+1 (415) 555-0198&#10;408-559-9314&#10;..."></textarea>
               <button id="bulk-ingestion-button" class="btn btn-muted">Process target batch</button>
               <div id="bulk-ingestion-results"></div>
             </div>
           </details>
          <div class="form-stack">
            <h3>Create or update Pro user</h3>
            <label for="admin-email">User email</label><input id="admin-email" type="email" placeholder="new-user@example.com">
            <label for="admin-user-password">Temporary password</label><input id="admin-user-password" type="password" placeholder="At least 8 characters">
            <button id="admin-add-user-button" class="btn btn-muted">Save Pro user</button>
          </div>
        </div>
      </section>
      <aside class="panel side-panel">
        <div class="side-block">
          <div class="side-title"><h3>Session tracking</h3><span class="badge badge-green">Live</span></div>
          <div class="session-row"><span>Last lookup</span><span id="session-last-lookup">None yet</span></div>
          <div class="session-row"><span>Pro user</span><span id="session-pro-user">Not signed in</span></div>
          <div class="session-row"><span>Database</span><span>digitscoper.db</span></div>
        </div>
        <div class="side-block">
          <div class="side-title"><h3>Quick start</h3></div>
          <div class="hint">Use <strong>Lookup</strong> to scan a number, <strong>Pro</strong> to save intelligence, and <strong>Admin</strong> to inspect the local ledger.</div>
        </div>
         <div class="hint"><strong>Privacy by design.</strong><br>Live lookup responses are requested only when you scan a number, then stored in the local SQLite database created beside the app.</div>
      </aside>
    </main>
    <footer>Digitscoper Desktop Engine <span id="copyright-year"></span> · Secure local utility</footer>
  </div>
  <script>
    const API_BASE = window.location.pathname.startsWith("/api") ? "/api" : "";
    const state = { proEmail: null, proTier: null, proToken: null, lastRecord: null };
    // Restore a previous Pro session (token only; identity is re-validated server-side).
    try {
      const saved = JSON.parse(localStorage.getItem("digitscoper_pro") || "null");
      if (saved && saved.token) {
        state.proToken = saved.token;
        state.proEmail = saved.email || null;
        state.proTier = saved.tier || null;
      }
    } catch (error) { /* storage unavailable */ }
    function persistProSession() {
      try {
        if (state.proToken) {
          localStorage.setItem("digitscoper_pro", JSON.stringify({ token: state.proToken, email: state.proEmail, tier: state.proTier }));
        } else {
          localStorage.removeItem("digitscoper_pro");
        }
      } catch (error) { /* storage unavailable */ }
    }
    function clearProSession() {
      state.proEmail = null; state.proTier = null; state.proToken = null;
      persistProSession();
    }
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" })[c]);
    const jsonLabel = (value) => esc(JSON.stringify(value, null, 2));
    const signalMarkup = (value) => Object.entries(value || {}).map(([key, enabled]) => {
      const label = key.replaceAll("_", " ");
      return "<span class='pill " + (enabled ? "yes" : "no") + "'>" + esc(label) + ": " + (enabled ? "Yes" : "No") + "</span>";
    }).join("");
    function setStatus(id, message, error = false) {
      const element = $(id); element.textContent = message; element.classList.toggle("error", error);
    }
    async function request(path, options = {}) {
      const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
      if (state.proToken) headers["Authorization"] = "Bearer " + state.proToken;
      const response = await fetch(API_BASE + path, { ...options, headers });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401 && state.proToken && path.startsWith("/pro")) {
        // Session expired or revoked — drop local state so the user can sign in again.
        clearProSession(); updateTopbarAuth();
        $("session-pro-user").textContent = "Not signed in";
        $("pro-login-form").style.display = "grid";
        $("pro-content").style.display = "none";
        throw new Error("Session expired. Please sign in again.");
      }
      if (!response.ok) throw new Error(body.detail || "Request failed");
      return body;
    }
    let areaCodeCatalog = [];
    function updateAreaCodeOptions() {
      const selectedState = $("pattern-state").value;
      const areaCodes = areaCodeCatalog.find((item) => item.code === selectedState)?.area_codes || [];
      $("pattern-area-code").innerHTML = "<option value='ALL'>All area codes (" + areaCodes.length + ")</option>" +
        areaCodes.map((code) => "<option value='" + esc(code) + "'>" + esc(code) + "</option>").join("");
    }
    async function loadAreaCodes() {
      const data = await request("/area-codes");
      areaCodeCatalog = data.states;
      $("pattern-state").innerHTML = areaCodeCatalog.map((item) =>
        "<option value='" + esc(item.code) + "'>" + esc(item.name) + "</option>"
      ).join("");
      $("pattern-state").value = "CA";
      updateAreaCodeOptions();
    }
    $("pattern-state").addEventListener("change", updateAreaCodeOptions);
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
      tab.classList.add("active"); $("view-" + tab.dataset.view).classList.add("active");
    }));
    // Topbar auth shortcut: jump straight to the Pro login form (or the Pro
    // workspace when already signed in) so sign-in is one tap from anywhere.
    function goToView(name) {
      const tab = document.querySelector('.tab[data-view="' + name + '"]');
      if (tab) tab.click();
    }
    function updateTopbarAuth() {
      const btn = $("topbar-auth-button");
      if (!btn) return;
      if (state.proEmail) {
        btn.textContent = "✓ Pro";
        btn.title = "Signed in as " + state.proEmail + " — open Pro workspace";
      } else {
        btn.textContent = "Sign in";
        btn.title = "Sign in to Pro";
      }
      syncCheckoutEmail();
    }
    // Keep the pricing-tab email row in sync with sign-in state: signed-in
    // users check out with their account email; guests enter one.
    function syncCheckoutEmail() {
      const row = $("checkout-email-row");
      const input = $("checkout-email");
      const hint = $("checkout-email-hint");
      if (!row || !input) return;
      if (state.proEmail) {
        input.value = state.proEmail;
        input.readOnly = true;
        input.style.opacity = "0.7";
        if (hint) hint.textContent = "Checking out as " + state.proEmail + " (your signed-in account).";
      } else {
        input.readOnly = false;
        input.style.opacity = "1";
        if (hint) hint.textContent = "New here? No signup needed — enter your email, pay, then set a password on the Pro tab.";
      }
    }
    $("topbar-auth-button").addEventListener("click", () => {
      goToView("pro");
      if (!state.proEmail) {
        const form = $("pro-login-form");
        if (form) form.scrollIntoView({ behavior: "smooth", block: "center" });
        const email = $("pro-email");
        if (email) window.setTimeout(() => email.focus({ preventScroll: true }), 350);
      }
    });
    updateTopbarAuth();
    // If a session token survived a reload, validate it against the server
    // (identity comes from the token, never from local state).
    (async () => {
      if (!state.proToken) return;
      try {
        const data = await request("/pro/dashboard");
        state.proEmail = data.email;
        state.proTier = data.tier;
        persistProSession();
        updateTopbarAuth();
        $("session-pro-user").textContent = data.email + " (" + state.proTier + ")";
        $("tier-badge").textContent = state.proTier === "pro_plus" ? "PRO+" : "PRO";
        $("pro-login-form").style.display = "none";
        await refreshDashboard();
        if (state.proTier === "pro_plus") {
          $("apikey-section").style.display = "block";
          await refreshApiKeys();
        }
      } catch (error) { /* token invalid — already cleared by request() */ }
    })();
    async function runLookup() {
      const number = $("lookup-number").value.trim();
      if (!number) return setStatus("lookup-status", "Enter a number to scan.", true);
       setStatus("lookup-status", "Querying live IPQualityScore phone intelligence...");
      try {
        const data = await request("/lookup/" + encodeURIComponent(number));
        state.lastRecord = data;
        $("lookup-result").style.display = "block";
        $("result-number").textContent = data.number;
        $("result-count").textContent = data.lookup_count + (data.lookup_count === 1 ? " scan" : " scans");
        $("carrier-name").textContent = data.carrier.name || "Unknown";
        $("carrier-line").textContent = data.carrier.line_type || "Unknown";
        $("carrier-region").textContent = data.carrier.region || "Unknown";
        $("carrier-tz").textContent = data.carrier.timezone || "Unknown";
        $("spam-score").textContent = data.spam.score + " / 100";
        $("spam-label").textContent = data.spam.label || "Unknown";
        $("line-active").textContent = data.line_status?.label || data.carrier.active_status || "Unknown";
        $("biz-listed").textContent = data.business.listed ? "Listed" : "Not listed";
        $("biz-name").textContent = data.business.name || "No business match";
        $("directories").innerHTML = signalMarkup(data.directories);
        $("public-records").innerHTML = signalMarkup(data.public_records);
        $("session-last-lookup").textContent = data.number;
        if (state.proEmail) {
          try {
            await request("/pro/auto_save", { method: "POST", body: JSON.stringify({
              number: data.number,
              carrier: data.carrier.name,
              line_type: data.carrier.line_type,
              region: data.carrier.region,
              business_name: data.business.name || ""
            }) });
            await refreshDashboard();
            setStatus("lookup-status", "Scan complete · saved to Pro history");
          } catch (error) {
            setStatus("lookup-status", "Scan complete · local cache updated");
          }
        } else {
          setStatus("lookup-status", "Scan complete · cached locally");
        }
      } catch (error) { setStatus("lookup-status", error.message, true); }
    }
    $("lookup-button").addEventListener("click", runLookup);
    $("lookup-number").addEventListener("keydown", (event) => { if (event.key === "Enter") runLookup(); });
    async function refreshDashboard() {
      const data = await request("/pro/dashboard");
      $("pro-content").style.display = "block";
      $("saved-number-count").textContent = data.analytics.total_saved_numbers;
      $("saved-pattern-count").textContent = data.analytics.total_saved_patterns;
      $("saved-numbers").innerHTML = data.saved_numbers.length ? data.saved_numbers.map((item) => {
        const number = esc(item.number);
        const carrier = esc(item.carrier || "Unknown");
        const business = esc(item.business_name || "None");
        return "<div class='data-card' style='display:flex; align-items:center; justify-content:space-between; gap:12px'><div style='text-align:left;'><strong style='font-family:monospace;'>" + number + "</strong><div style='font-size:11px; color:#64748b; margin-top:2px;'>" + carrier + " &middot; <span style='color:#34d399;'>" + business + "</span></div></div><button class='btn-danger' style='padding:4px 8px; font-size:11px;' onclick=\"if(confirm('Delete?')) deleteSavedNumber('" + number + "')\">Delete</button></div>";
      }).join("") : "<span class='empty'>No numbers saved yet.</span>";
      $("saved-patterns").innerHTML = data.saved_patterns.length ? data.saved_patterns.map((item) => "<span class='pill'>" + esc(item.pattern) + (item.area_code ? " · " + esc(item.area_code) : "") + "</span>").join("") : "<span class='empty'>No patterns saved yet.</span>";
    }
    async function deleteSavedNumber(number) {
      try {
        await request("/pro/delete_number/" + encodeURIComponent(number), { method: "DELETE" });
        await refreshDashboard();
      } catch (error) { setStatus("pro-status", error.message, true); }
    }
    $("pro-login-button").addEventListener("click", async () => {
      try {
        const data = await request("/pro/login", { method: "POST", body: JSON.stringify({ email: $("pro-email").value.trim(), password: $("pro-password").value }) });
        if (!data.pro) throw new Error("This account does not have Pro access.");
        if (!data.token) throw new Error("Sign-in failed. Please try again.");
        state.proEmail = data.email;
        state.proTier = data.tier || "pro";
        state.proToken = data.token;
        persistProSession();
        updateTopbarAuth();
        $("session-pro-user").textContent = data.email + " (" + state.proTier + ")";
        $("tier-badge").textContent = state.proTier === "pro_plus" ? "PRO+" : "PRO";
        $("pro-login-form").style.display = "none";
        setStatus("pro-status", "Pro workspace unlocked.");
        await refreshDashboard();
        if (state.proTier === "pro_plus") {
          $("apikey-section").style.display = "block";
          await refreshApiKeys();
        } else {
          $("apikey-section").style.display = "none";
        }
      } catch (error) { setStatus("pro-status", error.message, true); }
    });
    $("pro-signout-button").addEventListener("click", async () => {
      try {
        await request("/pro/logout", { method: "POST" });
      } catch (error) { /* best effort */ }
      clearProSession();
      updateTopbarAuth();
      $("apikey-section").style.display = "none";
      $("apikey-new").textContent = "";
      $("session-pro-user").textContent = "Not signed in";
      $("pro-login-form").style.display = "grid";
      $("pro-content").style.display = "none";
      $("pattern-results").innerHTML = "";
      setStatus("pro-status", "Signed out of the Pro workspace.");
    });
    $("save-number-button").addEventListener("click", async () => {
      try {
        await request("/pro/save_number", { method: "POST", body: JSON.stringify({ number: $("save-number").value.trim() }) });
        $("save-number").value = ""; setStatus("pro-status", "Number saved."); await refreshDashboard();
      } catch (error) { setStatus("pro-status", error.message, true); }
    });
    $("save-pattern-button").addEventListener("click", async () => {
      try {
        const pattern = $("save-pattern").value.trim();
        const areaCode = pattern.match(/\b(\d{3})\b/)?.[1] || "";
        await request("/pro/save_pattern", { method: "POST", body: JSON.stringify({ pattern, area_code: areaCode }) });
        $("save-pattern").value = ""; setStatus("pro-status", "Pattern saved."); await refreshDashboard();
      } catch (error) { setStatus("pro-status", error.message, true); }
    });
    async function generateSuffixCombinations() {
      const suffix = $("pattern-suffix").value.trim();
      const areaCode = $("pattern-area-code").value;
      const stateCode = $("pattern-state").value;
      const scope = areaCode === "ALL" ? stateCode : areaCode;
      if (!/^\d{4}$/.test(suffix)) {
        setStatus("pro-status", "Enter exactly four digits for the pattern builder.", true);
        return;
      }
      const data = await request("/pattern_search/" + scope + "/" + suffix);
      if (!data.matches.length) {
        $("pattern-results").innerHTML = "<div class='empty'>No matching numbers exist in the local lookup ledger yet.</div>";
      } else {
        $("pattern-results").innerHTML = data.matches.map((item) => {
          const active = item.active === true ? "Active" : item.active === false ? "Inactive" : item.active_status || "Unknown status";
          const details = [item.carrier, item.line_type, item.business, "Risk " + (item.spam_score ?? "—"), active].filter(Boolean).join(" · ");
          return "<div class='pattern-option'><div><strong>" + esc(item.number) + "</strong><div class='hint'>" + esc(details) + "</div></div><button class='btn btn-muted' data-number='" + esc(item.number) + "'>Scan</button></div>";
        }).join("");
      }
      $("pattern-results").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
        $("lookup-number").value = button.dataset.number;
        document.querySelector("[data-view='lookup']").click();
        runLookup();
      }));
      try {
        await request("/pro/save_pattern", { method: "POST", body: JSON.stringify({
          pattern: scope + "-xxx-" + suffix,
          area_code: areaCode === "ALL" ? stateCode : areaCode
        }) });
        await refreshDashboard();
        setStatus("pro-status", data.matches.length ? "Ledger matches found and pattern saved." : "No ledger matches; pattern saved for later.");
      } catch (error) { setStatus("pro-status", error.message, true); }
    }
    $("pattern-generate-button").addEventListener("click", generateSuffixCombinations);
    // Admin requests authenticate via the X-Admin-Password header —
    // credentials never travel in URL query strings.
    function adminHeaders() {
      return { "X-Admin-Password": $("admin-password").value };
    }
    $("admin-load-button").addEventListener("click", async () => {
      try {
        const data = await request("/admin/db", { headers: adminHeaders() });
        setStatus("admin-status", "Loaded " + data.total_numbers + " lookup records.");
        $("admin-output").innerHTML = data.numbers.length ? data.numbers.map((item) => "<div class='db-row'><span>" + esc(item.number) + "</span><span>" + item.lookup_count + " scans</span></div>").join("") : "<div class='empty'>No lookup records yet.</div>";
      } catch (error) { setStatus("admin-status", error.message, true); }
    });
    $("admin-add-user-button").addEventListener("click", async () => {
      try {
        const data = await request("/admin/add_user", { method: "POST", body: JSON.stringify({ password: $("admin-password").value, email: $("admin-email").value.trim(), user_password: $("admin-user-password").value, is_pro: true }) });
        setStatus("admin-status", "Saved Pro user: " + data.email);
      } catch (error) { setStatus("admin-status", error.message, true); }
    });
    $("bulk-ingestion-button").addEventListener("click", async () => {
      const numbers = $("bulk-targets").value.trim();
      if (!numbers) return setStatus("admin-status", "Paste at least one target number to process.", true);
      try {
        const data = await request("/admin/bulk_seed", { method: "POST", body: JSON.stringify({
          password: $("admin-password").value,
          numbers
        }) });
        const rejected = data.rejected.length
          ? "<div class='ingestion-list'>" + data.rejected.map((item) => "<div>Line " + esc(item.line) + " · " + esc(item.value) + " · " + esc(item.reason) + "</div>").join("") + "</div>"
          : "<div class='hint'>No rejected entries.</div>";
        $("bulk-ingestion-results").innerHTML =
          "<div class='ingestion-summary'>" +
          "<div class='ingestion-card'><strong>" + data.seeded_count + "</strong><span>seeded targets</span></div>" +
          "<div class='ingestion-card'><strong>" + data.already_indexed_count + "</strong><span>already indexed</span></div>" +
          "<div class='ingestion-card'><strong>" + data.rejected_count + "</strong><span>rejected entries</span></div>" +
          "</div><div class='hint' style='margin-top:10px'>" + esc(data.note) + "</div>" +
          "<div style='margin-top:10px'><h3>Validation output</h3>" + rejected + "</div>";
        setStatus("admin-status", "Processed " + data.submitted + " batch entries.");
        const refreshed = await request("/admin/db", { headers: adminHeaders() });
        $("admin-output").innerHTML = refreshed.numbers.length ? refreshed.numbers.map((item) => "<div class='db-row'><span>" + esc(item.number) + "</span><span>" + item.lookup_count + " scans</span></div>").join("") : "<div class='empty'>No lookup records yet.</div>";
      } catch (error) { setStatus("admin-status", error.message, true); }
    });
    // ---------- Premium: exports ----------
    async function downloadExport(format) {
      if (!state.proToken) return setStatus("pro-status", "Sign in first.", true);
      try {
        setStatus("pro-status", "Preparing " + format.toUpperCase() + " export...");
        const headers = { "Authorization": "Bearer " + state.proToken };
        const response = await fetch(API_BASE + "/pro/export/" + format, { headers });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail || "Export failed");
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "digitscoper-pro-export." + format;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        setStatus("pro-status", format.toUpperCase() + " export downloaded.");
      } catch (error) { setStatus("pro-status", error.message, true); }
    }
    $("export-csv-button").addEventListener("click", () => downloadExport("csv"));
    $("export-pdf-button").addEventListener("click", () => downloadExport("pdf"));

    // ---------- Premium: tier badge + API keys ----------
    async function refreshApiKeys() {
      try {
        const data = await request("/proplus/api_keys");
        $("apikey-list").innerHTML = data.keys.length ? data.keys.map((k) =>
          "<div class='pattern-option'><div><strong style='font-family:monospace;'>" + esc(k.key_prefix) + "</strong>" +
          "<div class='hint'>" + esc(k.name) + " · created " + esc(k.created_at) +
          (k.last_used ? " · last used " + esc(k.last_used) : " · never used") + "</div></div>" +
          "<button class='btn btn-muted' data-keyid='" + k.id + "'>Revoke</button></div>"
        ).join("") : "<span class='empty'>No API keys yet.</span>";
        $("apikey-list").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
          if (!confirm("Revoke this API key?")) return;
          await request("/proplus/api_keys/" + btn.dataset.keyid, { method: "DELETE" });
          setStatus("pro-status", "API key revoked.");
          await refreshApiKeys();
        }));
      } catch (error) { $("apikey-list").innerHTML = "<span class='empty'>" + esc(error.message) + "</span>"; }
    }
    $("apikey-create-button").addEventListener("click", async () => {
      try {
        const data = await request("/proplus/api_keys", { method: "POST", body: JSON.stringify({ name: $("apikey-name").value.trim() }) });
        $("apikey-new").textContent = "New key (copy now — shown once): " + data.key;
        $("apikey-name").value = "";
        await refreshApiKeys();
      } catch (error) { setStatus("pro-status", error.message, true); }
    });

    // ---------- Premium: bulk lookup ----------
    $("bulk-run-button").addEventListener("click", async () => {
      if (!state.proEmail) return setStatus("bulk-status", "Sign in to Pro first (Pro tab).", true);
      const numbers = $("bulk-numbers").value.trim();
      if (!numbers) return setStatus("bulk-status", "Paste at least one number.", true);
      setStatus("bulk-status", "Running bulk scan...");
      try {
        const data = await request("/pro/bulk_lookup", { method: "POST", body: JSON.stringify({ numbers }) });
        setStatus("bulk-status", "Scanned " + data.processed + " of " + data.submitted + " numbers" + (data.truncated ? " (batch limit " + data.limit + ")" : "") + ".");
        $("bulk-results").innerHTML = data.results.length ?
          "<table style='width:100%; border-collapse:collapse; font-size:12px;'><thead><tr style='color:var(--muted); text-align:left;'>" +
          "<th style='padding:8px; border-bottom:1px solid var(--line);'>Number</th><th style='padding:8px; border-bottom:1px solid var(--line);'>Carrier</th>" +
          "<th style='padding:8px; border-bottom:1px solid var(--line);'>Line</th><th style='padding:8px; border-bottom:1px solid var(--line);'>Region</th>" +
          "<th style='padding:8px; border-bottom:1px solid var(--line);'>Risk</th><th style='padding:8px; border-bottom:1px solid var(--line);'>Status</th></tr></thead><tbody>" +
          data.results.map((r) => "<tr><td style='padding:8px; border-bottom:1px solid var(--line); font-family:monospace;'>" + esc(r.number) + "</td>" +
            "<td style='padding:8px; border-bottom:1px solid var(--line);'>" + esc(r.carrier || "—") + "</td>" +
            "<td style='padding:8px; border-bottom:1px solid var(--line);'>" + esc(r.line_type || "—") + "</td>" +
            "<td style='padding:8px; border-bottom:1px solid var(--line);'>" + esc([r.city, r.state].filter(Boolean).join(", ") || r.region || "—") + "</td>" +
            "<td style='padding:8px; border-bottom:1px solid var(--line);'>" + esc((r.spam_score ?? "—") + " · " + (r.spam_label || "")) + "</td>" +
            "<td style='padding:8px; border-bottom:1px solid var(--line);'>" + esc(r.line_status || "—") + "</td></tr>").join("") +
          "</tbody></table>" +
          (data.errors.length ? "<p class='hint' style='margin-top:8px;'>" + data.errors.length + " lookups failed.</p>" : "")
          : "<span class='empty'>No results.</span>";
      } catch (error) { setStatus("bulk-status", error.message, true); }
    });

    // ---------- Premium: fraud network ----------
    function renderFraudGraph(nodes, links) {
      if (!nodes.length) { $("fraud-graph").innerHTML = ""; return; }
      const W = 640, H = 380, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 50;
      const pos = {};
      nodes.forEach((node, i) => {
        const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
        pos[node.number] = { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
      });
      const riskColor = (risk) => risk === "High risk" ? "#ff7d96" : risk === "Suspicious" ? "#ffb86b" : "#6ce3da";
      let svg = "<svg viewBox='0 0 " + W + " " + H + "' style='width:100%; max-width:640px; border:1px solid var(--line); border-radius:14px; background:rgba(10,17,30,.6);'>";
      links.forEach((l) => {
        const a = pos[l.from], b = pos[l.to];
        if (!a || !b) return;
        svg += "<line x1='" + a.x.toFixed(1) + "' y1='" + a.y.toFixed(1) + "' x2='" + b.x.toFixed(1) + "' y2='" + b.y.toFixed(1) +
          "' stroke='rgba(255,125,150,.45)' stroke-width='1.5'><title>" + esc(l.reason) + "</title></line>";
      });
      nodes.forEach((node) => {
        const p = pos[node.number];
        svg += "<circle cx='" + p.x.toFixed(1) + "' cy='" + p.y.toFixed(1) + "' r='16' fill='" + riskColor(node.risk) + "' fill-opacity='.85'>" +
          "<title>" + esc(node.number + " · " + node.carrier + " · " + node.risk) + "</title></circle>" +
          "<text x='" + p.x.toFixed(1) + "' y='" + (p.y + 30).toFixed(1) + "' text-anchor='middle' fill='#8ea0b8' font-size='9' font-family='monospace'>" +
          esc(node.number.slice(-4)) + "</text>";
      });
      svg += "</svg>";
      $("fraud-graph").innerHTML = svg;
    }
    $("fraud-run-button").addEventListener("click", async () => {
      if (!state.proEmail) return setStatus("fraud-status", "Sign in to Pro+ first (Pro tab).", true);
      const numbers = $("fraud-numbers").value.trim();
      if (!numbers) return setStatus("fraud-status", "Paste at least one suspect number.", true);
      setStatus("fraud-status", "Analyzing network links...");
      try {
        const data = await request("/proplus/fraud_network", { method: "POST", body: JSON.stringify({ numbers }) });
        setStatus("fraud-status", "Analyzed " + data.analyzed + " numbers · " + data.cluster_count + " linked cluster" + (data.cluster_count === 1 ? "" : "s") + " · " + data.linked_numbers + " numbers linked.");
        $("fraud-summary").innerHTML =
          "<div class='ingestion-summary'>" +
          "<div class='ingestion-card'><strong>" + data.analyzed + "</strong><span>numbers analyzed</span></div>" +
          "<div class='ingestion-card'><strong>" + data.cluster_count + "</strong><span>fraud clusters</span></div>" +
          "<div class='ingestion-card'><strong>" + data.linked_numbers + "</strong><span>linked numbers</span></div></div>";
        renderFraudGraph(data.nodes, data.links);
        $("fraud-clusters").innerHTML = data.clusters.length ? data.clusters.map((c) =>
          "<div class='data-card' style='" + (c.threat === "HIGH" ? "border-color:rgba(255,125,150,.5);" : "") + "'>" +
          "<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'>" +
          "<strong>Cluster #" + c.id + " · " + c.size + " numbers</strong>" +
          "<span class='badge " + (c.threat === "HIGH" ? "badge-green" : "badge-blue") + "' style='" + (c.threat === "HIGH" ? "color:var(--danger); background:rgba(255,125,150,.12);" : "") + "'>" + c.threat + "</span></div>" +
          "<div class='hint' style='margin-bottom:8px;'>Shared: " + esc(Object.entries(c.shared_traits).map(([k, v]) => k + "=" + v).join(" · ") || "multiple weak signals") + "</div>" +
          "<div class='saved-list'>" + c.members.map((m) => "<span class='pill'>" + esc(m) + "</span>").join("") + "</div></div>"
        ).join("") : "<span class='empty'>No linked clusters found — these numbers don't share enough signals to form an operation.</span>";
      } catch (error) { setStatus("fraud-status", error.message, true); }
    });

    // ---------- Premium: Stripe checkout ----------
    // Signed-in users check out with their account email. New customers
    // check out with just an email address — the account is created by the
    // Stripe webhook and they set a password on return (?checkout=success).
    async function startCheckout(tier) {
      let email = state.proEmail;
      if (!email) {
        const input = $("checkout-email");
        email = (input ? input.value : "").trim().toLowerCase();
        if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email)) {
          setStatus("pricing-status", "Enter a valid email address to start checkout.", true);
          if (input) input.focus();
          return;
        }
      }
      setStatus("pricing-status", "Creating secure checkout...");
      try {
        const data = await request("/stripe/create-checkout", { method: "POST", body: JSON.stringify({ email, tier }) });
        window.location.href = data.url;
      } catch (error) { setStatus("pricing-status", error.message, true); }
    }
    $("checkout-pro-button").addEventListener("click", () => startCheckout("pro"));
    $("checkout-proplus-button").addEventListener("click", () => startCheckout("pro_plus"));

    // After a successful Stripe checkout the customer lands back with
    // ?checkout=success&session_id=... — new customers set their password
    // here (verified against the paid Stripe session) and are signed
    // straight in. Signed-in customers upgrading keep their credentials.
    (function handleCheckoutReturn() {
      const params = new URLSearchParams(window.location.search);
      if (params.get("checkout") === "cancelled") {
        setStatus("pricing-status", "Checkout was cancelled. No charge was made.", true);
      }
      const sessionId = params.get("session_id");
      if (params.get("checkout") !== "success" || !sessionId) return;
      goToView("pro");
      // Clean the session_id out of the address bar for both branches.
      const cleanUrl = () => window.history.replaceState(null, "", window.location.pathname);
      if (state.proEmail) {
        setStatus("pro-status", "Payment received! Your tier is upgrading — refresh the Pro workspace in a moment.");
        cleanUrl();
        return;
      }
      $("pro-login-form").style.display = "none";
      $("pro-setup-form").style.display = "grid";
      setStatus("pro-status", "Payment received! Set a password to unlock your Pro workspace.");
      $("pro-setup-button").addEventListener("click", async () => {
        const email = $("setup-email").value.trim();
        const newPassword = $("setup-password").value;
        if (!email || !newPassword) return setStatus("pro-status", "Enter your email and a new password.", true);
        if (newPassword.length < 8) return setStatus("pro-status", "Password must be at least 8 characters.", true);
        try {
          const data = await request("/pro/set_password", { method: "POST", body: JSON.stringify({ email, session_id: sessionId, new_password: newPassword }) });
          state.proEmail = data.email;
          state.proTier = data.tier || "pro";
          state.proToken = data.token;
          persistProSession();
          updateTopbarAuth();
          $("session-pro-user").textContent = data.email + " (" + state.proTier + ")";
          $("tier-badge").textContent = state.proTier === "pro_plus" ? "PRO+" : "PRO";
          $("pro-setup-form").style.display = "none";
          setStatus("pro-status", "Password set. Pro workspace unlocked.");
          await refreshDashboard();
          if (state.proTier === "pro_plus") {
            $("apikey-section").style.display = "block";
            await refreshApiKeys();
          }
          // Clean the session_id out of the address bar.
          window.history.replaceState(null, "", window.location.pathname);
        } catch (error) { setStatus("pro-status", error.message, true); }
      });
    })();

    loadAreaCodes().catch((error) => setStatus("pro-status", "Area-code catalog unavailable: " + error.message, true));
    $("copyright-year").textContent = new Date().getFullYear();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/api", response_class=HTMLResponse)
@app.get("/api/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


def run_server() -> None:
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


def run_desktop() -> None:
    import webview

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(0.8)
    webview.create_window(
        "Digitscoper Desktop",
        f"http://127.0.0.1:{PORT}/",
        width=1220,
        height=820,
        min_size=(860, 620),
    )
    webview.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Digitscoper Desktop Engine")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Open the embedded UI in a native pywebview window.",
    )
    args = parser.parse_args()
    if args.desktop or os.environ.get("DIGITSCOPER_DESKTOP") == "1":
        run_desktop()
    else:
        run_server()
