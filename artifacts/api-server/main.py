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

The database is created next to this file as digitscoper.db. The seeded Pro
account is ronald@example.com / password123. Set ADMIN_PASSWORD in the
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
from pydantic import BaseModel, Field
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


init_db()


class ProLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SaveNumberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    number: str = Field(min_length=1, max_length=40)


class AutoSaveRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    number: str = Field(min_length=1, max_length=40)
    carrier: str = Field(default="", max_length=120)
    line_type: str = Field(default="", max_length=40)
    region: str = Field(default="", max_length=120)
    business_name: str = Field(default="", max_length=160)


class SavePatternRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
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


class BulkLookupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    numbers: str = Field(min_length=1, max_length=100000)


class FraudNetworkRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    numbers: str = Field(min_length=1, max_length=100000)


class ApiKeyCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default="", max_length=80)


app = FastAPI(
    title="Digitscoper Desktop Engine",
    description="Unified phone lookup, Pro saves, and local SQLite administration.",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def lookup_record(number: str) -> dict[str, Any]:
    normalized = normalize_number(number)
    now = utc_now()
    live = live_record(normalized, fetch_ipqs_record(normalized), now)
    with connection() as db:
        row = db.execute(
            "SELECT * FROM lookups WHERE number = ?", (normalized,)
        ).fetchone()
        if row:
            lookup_count = row["lookup_count"] + 1
            db.execute(
                """
                UPDATE lookups SET carrier = ?, spam = ?, business = ?,
                    directories = ?, public_records = ?, region = ?,
                    last_seen = ?, lookup_count = ?
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
                    lookup_count,
                    normalized,
                ),
            )
            live.update({"first_seen": row["first_seen"], "last_seen": now, "lookup_count": lookup_count})
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
def lookup(number: str) -> dict[str, Any]:
    return lookup_record(number)


def _safe_json(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@app.get("/history")
@app.get("/api/history")
def history() -> dict[str, Any]:
    """Return recent lookup history, most recent first."""
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
def clear_history() -> dict[str, Any]:
    """Clear the local lookup history ledger."""
    with connection() as db:
        result = db.execute("DELETE FROM lookups")
        return {"cleared": result.rowcount}


@app.get("/number_finder")
@app.get("/api/number_finder")
def number_finder(suffix: str = Query(...), state: str = Query("")) -> dict[str, Any]:
    """Find numbers in the local ledger ending in a 4-digit suffix,
    grouped by state then city. Local SQLite only — no external API calls."""
    if not re.fullmatch(r"\d{4}", suffix or ""):
        raise HTTPException(status_code=400, detail="Suffix must be exactly 4 digits.")
    state_filter = (state or "").upper().strip()
    if state_filter and state_filter not in STATE_AREA_CODES:
        raise HTTPException(status_code=400, detail="Unknown state code.")

    with connection() as db:
        rows = db.execute(
            "SELECT number, carrier, spam, region, last_seen, lookup_count "
            "FROM lookups WHERE number LIKE ? ORDER BY last_seen DESC",
            (f"%{suffix}",),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        region = _safe_json(row["region"])
        carrier = _safe_json(row["carrier"])
        spam = _safe_json(row["spam"])
        st = (region.get("state") or "").upper().strip() or "??"
        if state_filter and st != state_filter:
            continue
        city = (region.get("city") or "").strip() or "Unknown city"
        entry = grouped.setdefault(st, {
            "state": st,
            "state_name": STATE_AREA_CODES.get(st, {}).get("name", st),
            "cities": {},
        })
        entry["cities"].setdefault(city, []).append({
            "number": row["number"],
            "carrier": carrier.get("name") or "Unknown",
            "line_type": carrier.get("line_type") or carrier.get("type") or "",
            "fraud_score": spam.get("score"),
            "risk_label": spam.get("label") or "",
            "last_seen": row["last_seen"],
            "lookup_count": row["lookup_count"],
        })

    results = []
    for st in sorted(grouped):
        entry = grouped[st]
        cities = [
            {"city": city, "count": len(nums), "numbers": nums}
            for city, nums in sorted(entry["cities"].items())
        ]
        results.append({
            "state": entry["state"],
            "state_name": entry["state_name"],
            "count": sum(c["count"] for c in cities),
            "cities": cities,
        })

    return {
        "suffix": suffix,
        "state_filter": state_filter or None,
        "total": sum(r["count"] for r in results),
        "results": results,
        "source": "Digitscoper local lookup ledger",
    }


@app.get("/area-codes")
@app.get("/api/area-codes")
def area_codes() -> dict[str, Any]:
    return {
        "states": [
            {"code": code, "name": value["name"], "area_codes": value["area_codes"]}
            for code, value in sorted(STATE_AREA_CODES.items(), key=lambda item: item[1]["name"])
        ]
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
def pro_login(req: ProLoginRequest) -> dict[str, Any]:
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
    return {"status": "ok", "pro": tier in (TIER_PRO, TIER_PROPLUS), "tier": tier, "email": req.email}


@app.post("/pro/save_number")
@app.post("/api/pro/save_number")
def save_number(req: SaveNumberRequest) -> dict[str, Any]:
    require_pro(req.email)
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
                req.email,
                record["number"],
                record["carrier"]["name"],
                record["carrier"]["line_type"],
                record["carrier"]["region"],
                record["business"]["name"] or "",
                utc_now(),
            ),
        )
    return dashboard_data(req.email)


@app.post("/pro/auto_save")
@app.post("/api/pro/auto_save")
def auto_save(req: AutoSaveRequest) -> dict[str, Any]:
    require_pro(req.email)
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
                req.email,
                normalized,
                req.carrier.strip(),
                req.line_type.strip(),
                req.region.strip(),
                req.business_name.strip(),
                utc_now(),
            ),
        )
    return {"status": "synchronized", **dashboard_data(req.email)}


@app.post("/pro/save_pattern")
@app.post("/api/pro/save_pattern")
def save_pattern(req: SavePatternRequest) -> dict[str, Any]:
    require_pro(req.email)
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
            (req.email, pattern, req.area_code.strip(), utc_now()),
        )
    return dashboard_data(req.email)


@app.get("/pro/dashboard")
@app.get("/api/pro/dashboard")
def pro_dashboard(email: str = Query(..., min_length=3)) -> dict[str, Any]:
    return dashboard_data(email)


def admin_check(password: str) -> None:
    if not password or not bcrypt.checkpw(
        password.encode("utf-8"),
        hash_password(ADMIN_PASSWORD).encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Invalid admin password.")


@app.get("/admin/db")
@app.get("/api/admin/db")
def admin_db(password: str = Query(..., min_length=1)) -> dict[str, Any]:
    admin_check(password)
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
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    tier = req.tier.strip().lower()
    price_id = {TIER_PRO: STRIPE_PRO_PRICE_ID, TIER_PROPLUS: STRIPE_PROPLUS_PRICE_ID}.get(tier)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid tier. Use 'pro' or 'pro_plus'.")
    email = req.email.strip().lower()
    try:
        session = stripe.checkout.Session.create(  # type: ignore[union-attr]
            mode="subscription",
            customer_email=email,
            line_items=[{"price": price_id, "quantity": 1}],
            metadata={"email": email, "tier": tier},
            success_url=os.environ.get("STRIPE_SUCCESS_URL", "/") + "?checkout=success",
            cancel_url=os.environ.get("STRIPE_CANCEL_URL", "/") + "?checkout=cancelled",
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Stripe checkout failed: {error}") from error
    return {"url": session.url, "session_id": session.id}


@app.post("/stripe/webhook")
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Verify the Stripe signature and fulfill subscription events."""
    if not _stripe_ready():
        raise HTTPException(status_code=503, detail="Stripe is not configured.")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)  # type: ignore[union-attr]
        else:
            event = stripe.Event.construct_from(json.loads(payload), STRIPE_SECRET_KEY)  # type: ignore[union-attr]
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {error}") from error

    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {}) or {}

    if event_type == "checkout.session.completed":
        email = (data_object.get("metadata", {}) or {}).get("email") or data_object.get("customer_email") or ""
        tier = (data_object.get("metadata", {}) or {}).get("tier", "").strip().lower()
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
                    # create the account on first successful payment; user sets
                    # password via admin or a future self-serve flow
                    db.execute(
                        "INSERT INTO users (email, password, is_pro, tier, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (email, hash_password(secrets.token_hex(16)), 1, tier, utc_now()),
                    )
        return {"status": "fulfilled", "email": email, "tier": tier}

    if event_type == "customer.subscription.deleted":
        # downgrade: find by customer email when available
        customer_id = data_object.get("customer")
        email = ""
        try:
            if customer_id:
                customer = stripe.Customer.retrieve(customer_id)  # type: ignore[union-attr]
                email = (customer.get("email") or "").strip().lower()
        except Exception:
            email = ""
        metadata = data_object.get("metadata", {}) or {}
        email = email or (metadata.get("email") or "").strip().lower()
        if email:
            set_user_tier(email, TIER_FREE)
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
def pro_export_csv(email: str = Query(..., min_length=3)) -> Response:
    """Download the Pro user's saved numbers as CSV."""
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
def pro_export_pdf(email: str = Query(..., min_length=3)) -> Response:
    """Download the Pro user's saved numbers as a PDF report."""
    tier = require_pro(email)
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
def pro_bulk_lookup(req: BulkLookupRequest) -> dict[str, Any]:
    """Run live lookups across a batch of numbers. Pro: 50/batch, Pro+: 200/batch."""
    tier = require_pro(req.email)
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
def proplus_fraud_network(req: FraudNetworkRequest) -> dict[str, Any]:
    """Flagship Pro+ feature: detect linked fraud networks across a batch.

    Numbers sharing 2+ attributes (carrier, region/state, risk label,
    line type, area code) are linked; connected components become clusters.
    """
    require_proplus(req.email)
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
def proplus_create_api_key(req: ApiKeyCreateRequest) -> dict[str, Any]:
    """Generate a new API key. The plain key is returned ONCE."""
    require_proplus(req.email)
    plain_key = "dsk_live_" + secrets.token_urlsafe(32)
    key_hash = _hash_api_key(plain_key)
    name = req.name.strip() or f"Key {utc_now()[:10]}"
    with connection() as db:
        cursor = db.execute(
            """
            INSERT INTO api_keys (key_hash, key_prefix, user_email, name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key_hash, plain_key[:12] + "...", req.email.strip().lower(), name, utc_now()),
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
def proplus_list_api_keys(email: str = Query(..., min_length=3)) -> dict[str, Any]:
    """List API keys (masked) for a Pro+ user."""
    require_proplus(email)
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
def proplus_delete_api_key(key_id: int, email: str = Query(..., min_length=3)) -> dict[str, Any]:
    """Revoke an API key."""
    require_proplus(email)
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
    .brand { display: flex; align-items: center; gap: 12px; min-width: 220px; cursor: pointer; }
    .brand-mark {
      width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px;
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      color: #05101d; font-weight: 900; box-shadow: 0 0 28px rgba(93, 184, 255, .26);
    }
    .brand-name { font-size: 14px; letter-spacing: .17em; font-weight: 800; }
    .brand-sub { color: var(--muted); font-size: 11px; margin-top: 2px; }
    .tabs { display: flex; gap: 6px; }
    .tab {
      color: var(--muted); background: transparent; border: 1px solid transparent;
      border-radius: 9px; padding: 9px 15px; transition: .2s ease;
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
    .badge-red { color: var(--danger); background: rgba(255,125,150,.12); }
    .badge-amber { color: #f5b544; background: rgba(245,181,68,.12); }
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
    .history-list { margin-top: 6px; border: 1px solid var(--line); border-radius: 14px; background: rgba(23, 34, 55, .48); overflow: hidden; }
    .history-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; background: none; border: none; border-bottom: 1px solid var(--line); padding: 12px 14px; color: var(--text); cursor: pointer; font-family: inherit; }
    .history-row:last-child { border-bottom: none; }
    .history-row:hover { background: rgba(93, 184, 255, .06); }
    .history-number { font-weight: 750; font-size: 14px; }
    .history-meta { font-size: 11px; color: var(--muted); margin-top: 3px; }
    .history-empty { padding: 26px 14px; text-align: center; color: var(--muted); font-size: 13px; }
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
    footer a { color: var(--blue); text-decoration: none; }
    footer a:hover { text-decoration: underline; }
    @media (max-width: 820px) {
      .topbar { align-items: flex-start; flex-wrap: wrap; }
      .top-status { margin-left: auto; }
      .tabs {
        min-width: 0; max-width: 100%; overflow-x: auto;
        -webkit-overflow-scrolling: touch; scrollbar-width: none;
      }
      .tabs::-webkit-scrollbar { display: none; }
      .tab { flex: 0 0 auto; white-space: nowrap; }
      .workspace { grid-template-columns: 1fr; }
      .side-panel { position: static; }
      /* Compact admin view on mobile */
      #view-admin .eyebrow { font-size: 9px; }
      #view-admin h2 { font-size: 17px; margin: 6px 0 4px; }
      #view-admin > p { font-size: 12px; line-height: 1.45; }
      #view-admin .form-stack { gap: 8px; margin-top: 14px; }
      #view-admin .form-stack h3 { font-size: 10px; margin-bottom: 6px; }
      #view-admin input { padding: 9px 11px; font-size: 14px; }
      #view-admin .btn { padding: 9px 13px; font-size: 13px; }
      #view-admin .bulk-ingestion { margin-top: 14px; }
      #view-admin .bulk-ingestion summary { padding: 11px 13px; font-size: 12px; }
      #view-admin .bulk-ingestion-body { gap: 8px; padding: 0 13px 13px; }
      #view-admin .bulk-ingestion-body textarea { min-height: 90px; padding: 9px 11px; font-size: 13px; }
      #view-admin .admin-output { margin-top: 12px; max-height: 200px; }
      #view-admin .db-row { padding: 7px 0; font-size: 11px; }
    }
    @media (max-width: 520px) {
      body { overflow-x: clip; }
      .tabs { width: 100%; order: 3; }
      .lookup-bar { flex-direction: column; }
      .data-grid { grid-template-columns: 1fr; }
      .data-card.wide { grid-column: auto; }
      .result-head { align-items: flex-start; flex-direction: column; }
      .pattern-controls { grid-template-columns: 1fr; }
      .ingestion-summary { grid-template-columns: 1fr; }
      /* Extra-compact admin view on phones */
      #view-admin h2 { font-size: 15px; }
      #view-admin .form-stack { margin-top: 10px; }
      #view-admin input { padding: 8px 10px; }
      #view-admin .btn { padding: 8px 11px; font-size: 12px; }
      #view-admin .bulk-ingestion summary { padding: 9px 11px; }
      #view-admin .bulk-ingestion-body textarea { min-height: 70px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand" onclick="document.querySelector(&quot;[data-view='lookup']&quot;).click()" title="Back to home">
        <div class="brand-mark">D</div>
        <div><div class="brand-name">DIGITSCOPER</div><div class="brand-sub">Desktop intelligence engine</div></div>
      </div>
      <nav class="tabs" aria-label="Primary navigation">
        <button class="tab active" data-view="lookup">Lookup</button>
        <button class="tab" data-view="pro">Pro</button>
        <button class="tab" data-view="bulk">Bulk Lookup</button>
        <button class="tab" data-view="fraud">Fraud Network</button>
        <button class="tab" data-view="pricing">Pricing</button>
        <button class="tab" data-view="history">History</button>
        <button class="tab" data-view="finder">Finder</button>
      </nav>
      <div class="top-status"><span class="dot"></span> Local engine online</div>
    </header>
    <main class="workspace">
      <section class="panel main-panel">
        <div id="view-lookup" class="view active">
          <div class="eyebrow">Unified phone lookup</div>
          <h1>See the signal<br>behind the number.</h1>
           <p class="intro">Run a live IPQualityScore scan across carrier, line status, business, risk, and reputation signals. Results are cached in your private SQLite engine for the next pass.</p>
           <p class="hint" style="margin: 2px 0 0;">Only shows numbers actually scanned in Digitscoper. No fake records.</p>
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
          <p class="hint" style="margin-top:10px;">After checkout, sign in on the <strong>Pro</strong> tab with the same email you paid with.</p>
        </div>
        <div id="view-history" class="view">
          <div class="eyebrow">Lookup history</div>
          <h2>Your past scans.</h2>
          <p class="intro">Every scan is cached in your private SQLite engine. Tap a row to re-run it.</p>
          <div id="history-status" class="status" role="status"></div>
          <div id="history-list" class="history-list"></div>
          <div style="margin-top:14px;">
            <button id="history-clear-button" class="btn btn-muted">Clear history</button>
          </div>
        </div>
        <div id="view-finder" class="view">
          <div class="eyebrow">Number finder</div>
          <h2>Track down a partial number.</h2>
          <p class="hint" style="margin: 2px 0 0;">Only shows numbers actually scanned in Digitscoper. No fake records.</p>
          <p class="intro">Enter the last 4 digits of a mystery number. Digitscoper searches numbers already saved in your local engine and shows where they cluster by state and city. No external lookups — crowd-sourced from your scans. Tap a number to run a full scan on it.</p>
          <div class="form-stack">
            <label for="finder-suffix">Last 4 digits</label>
            <input id="finder-suffix" inputmode="numeric" maxlength="4" placeholder="5016">
            <label for="finder-state">State (optional)</label>
            <select id="finder-state"><option value="">All states</option></select>
            <button id="finder-search-button" class="btn btn-primary">Search local database</button>
          </div>
          <div id="finder-status" class="status" role="status"></div>
          <div id="finder-results"></div>
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
    <footer>Digitscoper Desktop Engine <span id="copyright-year"></span> · Secure local utility · Need help? <a href="mailto:landlordai.team@gmail.com">Contact support</a></footer>
  </div>
  <script>
    const API_BASE = window.location.pathname.startsWith("/api") ? "/api" : "";
    const state = { proEmail: null, proTier: null, lastRecord: null };
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
      const response = await fetch(API_BASE + path, { headers: { "Content-Type": "application/json" }, ...options });
      const body = await response.json().catch(() => ({}));
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
    function showView(name) {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
      const tab = document.querySelector(".tab[data-view='" + name + "']");
      if (tab) tab.classList.add("active");
      const view = $("view-" + name);
      if (view) view.classList.add("active");
    }
    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
      if (location.hash) history.replaceState(null, "", location.pathname);
      showView(tab.dataset.view);
      if (tab.dataset.view === "history") loadHistory();
    }));
    // Hidden admin access: digitscoper.onrender.com/#admin
    function openFromHash() { if (location.hash === "#admin") showView("admin"); }
    window.addEventListener("hashchange", openFromHash);
    openFromHash();
    // ---------- History ----------
    function riskBadge(score, label) {
      const cls = score >= 75 ? "badge badge-red" : score >= 40 ? "badge badge-amber" : "badge badge-green";
      return "<span class='" + cls + "'>" + esc(label || (score + "/100")) + "</span>";
    }
    async function loadHistory() {
      const list = $("history-list");
      setStatus("history-status", "Loading history...");
      try {
        const data = await request("/history");
        const items = data.items || [];
        setStatus("history-status", items.length ? items.length + (items.length === 1 ? " scan" : " scans") + " on record." : "");
        list.innerHTML = items.length ? items.map((item) => {
          const place = [item.city, item.state].filter(Boolean).join(", ");
          const meta = [item.carrier, item.line_type, place].filter(Boolean).join(" · ");
          const when = item.last_seen ? new Date(item.last_seen).toLocaleString() : "";
          const scans = item.lookup_count > 1 ? " · " + item.lookup_count + " scans" : "";
          return "<button class='history-row' data-number='" + esc(item.number) + "'>" +
            "<span><span class='history-number'>" + esc(item.number) + "</span>" +
            "<span class='history-meta'>" + esc(meta) + (when ? "<br>" + esc(when) + esc(scans) : "") + "</span></span>" +
            riskBadge(item.fraud_score ?? 0, item.risk_label) +
            "</button>";
        }).join("") : "<div class='history-empty'>No lookups yet.<br>Scan a number to get started.</div>";
      } catch (error) {
        setStatus("history-status", error.message, true);
        list.innerHTML = "";
      }
    }
    $("history-list").addEventListener("click", (event) => {
      const row = event.target.closest(".history-row");
      if (!row) return;
      $("lookup-number").value = row.dataset.number;
      document.querySelector("[data-view='lookup']").click();
      runLookup();
    });
    $("history-clear-button").addEventListener("click", async () => {
      if (!confirm("Clear all lookup history? This cannot be undone.")) return;
      try {
        const data = await request("/history", { method: "DELETE" });
        setStatus("history-status", "Cleared " + data.cleared + " record" + (data.cleared === 1 ? "" : "s") + ".");
        loadHistory();
      } catch (error) { setStatus("history-status", error.message, true); }
    });
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
              email: state.proEmail,
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
      const data = await request("/pro/dashboard?email=" + encodeURIComponent(state.proEmail));
      $("pro-content").style.display = "block";
      $("saved-number-count").textContent = data.analytics.total_saved_numbers;
      $("saved-pattern-count").textContent = data.analytics.total_saved_patterns;
      $("saved-numbers").innerHTML = data.saved_numbers.length ? data.saved_numbers.map((item) => {
        const number = esc(item.number);
        const carrier = esc(item.carrier || "Unknown");
        const business = esc(item.business_name || "None");
        const deletePath = API_BASE + "/pro/delete_number/" + encodeURIComponent(state.proEmail) + "/" + encodeURIComponent(item.number);
        return "<div class='data-card' style='display:flex; align-items:center; justify-content:space-between; gap:12px'><div style='text-align:left;'><strong style='font-family:monospace;'>" + number + "</strong><div style='font-size:11px; color:#64748b; margin-top:2px;'>" + carrier + " &middot; <span style='color:#34d399;'>" + business + "</span></div></div><button class='btn-danger' style='padding:4px 8px; font-size:11px;' onclick=\"if(confirm('Delete?')) fetch('" + deletePath + "', {method:'DELETE'}).then(() => refreshDashboard())\">Delete</button></div>";
      }).join("") : "<span class='empty'>No numbers saved yet.</span>";
      $("saved-patterns").innerHTML = data.saved_patterns.length ? data.saved_patterns.map((item) => "<span class='pill'>" + esc(item.pattern) + (item.area_code ? " · " + esc(item.area_code) : "") + "</span>").join("") : "<span class='empty'>No patterns saved yet.</span>";
    }
    $("pro-login-button").addEventListener("click", async () => {
      try {
        const data = await request("/pro/login", { method: "POST", body: JSON.stringify({ email: $("pro-email").value.trim(), password: $("pro-password").value }) });
        if (!data.pro) throw new Error("This account does not have Pro access.");
        state.proEmail = data.email;
        state.proTier = data.tier || "pro";
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
    $("pro-signout-button").addEventListener("click", () => {
      state.proEmail = null;
      state.proTier = null;
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
        await request("/pro/save_number", { method: "POST", body: JSON.stringify({ email: state.proEmail, number: $("save-number").value.trim() }) });
        $("save-number").value = ""; setStatus("pro-status", "Number saved."); await refreshDashboard();
      } catch (error) { setStatus("pro-status", error.message, true); }
    });
    $("save-pattern-button").addEventListener("click", async () => {
      try {
        const pattern = $("save-pattern").value.trim();
        const areaCode = pattern.match(/\b(\d{3})\b/)?.[1] || "";
        await request("/pro/save_pattern", { method: "POST", body: JSON.stringify({ email: state.proEmail, pattern, area_code: areaCode }) });
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
          email: state.proEmail,
          pattern: scope + "-xxx-" + suffix,
          area_code: areaCode === "ALL" ? stateCode : areaCode
        }) });
        await refreshDashboard();
        setStatus("pro-status", data.matches.length ? "Ledger matches found and pattern saved." : "No ledger matches; pattern saved for later.");
      } catch (error) { setStatus("pro-status", error.message, true); }
    }
    $("pattern-generate-button").addEventListener("click", generateSuffixCombinations);
    $("admin-load-button").addEventListener("click", async () => {
      try {
        const data = await request("/admin/db?password=" + encodeURIComponent($("admin-password").value), { headers: {} });
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
        const refreshed = await request("/admin/db?password=" + encodeURIComponent($("admin-password").value), { headers: {} });
        $("admin-output").innerHTML = refreshed.numbers.length ? refreshed.numbers.map((item) => "<div class='db-row'><span>" + esc(item.number) + "</span><span>" + item.lookup_count + " scans</span></div>").join("") : "<div class='empty'>No lookup records yet.</div>";
      } catch (error) { setStatus("admin-status", error.message, true); }
    });
    // ---------- Premium: exports ----------
    $("export-csv-button").addEventListener("click", () => {
      if (!state.proEmail) return setStatus("pro-status", "Sign in first.", true);
      window.location.href = API_BASE + "/pro/export/csv?email=" + encodeURIComponent(state.proEmail);
    });
    $("export-pdf-button").addEventListener("click", () => {
      if (!state.proEmail) return setStatus("pro-status", "Sign in first.", true);
      window.location.href = API_BASE + "/pro/export/pdf?email=" + encodeURIComponent(state.proEmail);
    });

    // ---------- Premium: tier badge + API keys ----------
    async function refreshApiKeys() {
      try {
        const data = await request("/proplus/api_keys?email=" + encodeURIComponent(state.proEmail));
        $("apikey-list").innerHTML = data.keys.length ? data.keys.map((k) =>
          "<div class='pattern-option'><div><strong style='font-family:monospace;'>" + esc(k.key_prefix) + "</strong>" +
          "<div class='hint'>" + esc(k.name) + " · created " + esc(k.created_at) +
          (k.last_used ? " · last used " + esc(k.last_used) : " · never used") + "</div></div>" +
          "<button class='btn btn-muted' data-keyid='" + k.id + "'>Revoke</button></div>"
        ).join("") : "<span class='empty'>No API keys yet.</span>";
        $("apikey-list").querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
          if (!confirm("Revoke this API key?")) return;
          await request("/proplus/api_keys/" + btn.dataset.keyid + "?email=" + encodeURIComponent(state.proEmail), { method: "DELETE" });
          setStatus("pro-status", "API key revoked.");
          await refreshApiKeys();
        }));
      } catch (error) { $("apikey-list").innerHTML = "<span class='empty'>" + esc(error.message) + "</span>"; }
    }
    $("apikey-create-button").addEventListener("click", async () => {
      try {
        const data = await request("/proplus/api_keys", { method: "POST", body: JSON.stringify({ email: state.proEmail, name: $("apikey-name").value.trim() }) });
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
        const data = await request("/pro/bulk_lookup", { method: "POST", body: JSON.stringify({ email: state.proEmail, numbers }) });
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
        const data = await request("/proplus/fraud_network", { method: "POST", body: JSON.stringify({ email: state.proEmail, numbers }) });
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
    async function startCheckout(tier) {
      if (!state.proEmail) {
        setStatus("pricing-status", "Sign in on the Pro tab first, then come back here.", true);
        document.querySelector("[data-view='pro']").click();
        return;
      }
      setStatus("pricing-status", "Creating secure checkout...");
      try {
        const data = await request("/stripe/create-checkout", { method: "POST", body: JSON.stringify({ email: state.proEmail, tier }) });
        window.location.href = data.url;
      } catch (error) { setStatus("pricing-status", error.message, true); }
    }
    $("checkout-pro-button").addEventListener("click", () => startCheckout("pro"));
    $("checkout-proplus-button").addEventListener("click", () => startCheckout("pro_plus"));

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