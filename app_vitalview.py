# app_vitalview.py  —  VitalView v2.1
# Deploy: streamlit run app_vitalview.py
# Requires: pip install streamlit pandas numpy altair plotly bcrypt
# Optional:  pip install xlsxwriter reportlab stripe pydeck

# ================================================================
# IMPORTS
# ================================================================
import os, time, sqlite3, logging, json, secrets, re
try:
    from supabase import create_client as _sb_create
    HAS_SUPABASE = True
except ImportError:
    HAS_SUPABASE = False
from pathlib import Path
from datetime import datetime
from io import BytesIO

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import plotly.express as px

# ── optional deps ───────────────────────────────────────────────
try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False

try:
    import xlsxwriter
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import simpleSplit
    HAS_PDF = True
except Exception:
    rl_canvas = None
    HAS_PDF = False

try:
    import stripe
    stripe.api_key = os.getenv("STRIPE_TEST_KEY", "")
    HAS_STRIPE = True
except Exception:
    stripe = None
    HAS_STRIPE = False

try:
    import pydeck as pdk
    HAS_PYDECK = True
except ImportError:
    pdk = None
    HAS_PYDECK = False

# ================================================================
# LOGGING
# ================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ================================================================
# LOGO  (inline SVG — crisp at any size, no file needed)
# ================================================================
def logo_img(size=48):
    """Returns an HTML img tag with an inline SVG VitalView logo."""
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
      <defs>
        <linearGradient id='lg' x1='0' y1='0' x2='1' y2='1'>
          <stop offset='0%' stop-color='#1a8fff'/>
          <stop offset='100%' stop-color='#00d4ff'/>
        </linearGradient>
        <filter id='glow'>
          <feGaussianBlur stdDeviation='1.5' result='blur'/>
          <feMerge><feMergeNode in='blur'/><feMergeNode in='SourceGraphic'/></feMerge>
        </filter>
      </defs>
      <!-- Shield body -->
      <path d='M32 4 L56 14 L56 36 Q56 52 32 60 Q8 52 8 36 L8 14 Z'
            fill='#0d1a2e' stroke='url(#lg)' stroke-width='2.5'/>
      <!-- Bold V letterform -->
      <polyline points='17,22 32,44 47,22'
                fill='none' stroke='url(#lg)' stroke-width='5.5'
                stroke-linecap='round' stroke-linejoin='round'
                filter='url(#glow)'/>
      <!-- Pulse dot -->
      <circle cx='32' cy='44' r='3.5' fill='#00d4ff' filter='url(#glow)'/>
    </svg>"""
    import base64 as _b64
    encoded = _b64.b64encode(svg.encode()).decode()
    return (
        f'<img src="data:image/svg+xml;base64,{encoded}" '
        f'alt="VitalView" '
        f'style="height:{size}px;width:{size}px;object-fit:contain;'
        f'display:block;filter:drop-shadow(0 0 6px rgba(0,180,255,0.45));">'
    )


# ================================================================
# CONSTANTS
# ================================================================
DB_PATH              = os.getenv("DB_PATH", "vitalview_users.db")
BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
COUNTY_GEOJSON_PATH  = os.path.join(BASE_DIR, "data", "us_counties.geojson")
STRIPE_PRICE_PRO     = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_ENT     = os.getenv("STRIPE_PRICE_ENT", "")
MAX_LOGIN_ATTEMPTS   = 5
LOCKOUT_SECONDS      = 300
VALID_PLANS          = {"free", "pro", "enterprise", "educator", "admin"}

PLAN_FEATURES = {
    "free":       {"exports": False, "ai_writer": False},
    "educator":   {"exports": False, "ai_writer": False},
    "pro":        {"exports": True,  "ai_writer": True},
    "enterprise": {"exports": True,  "ai_writer": True},
    "admin":      {"exports": True,  "ai_writer": True},
}

# ================================================================
# THEME
# ================================================================
THEME = {
    "bg":      "#111827",
    "card":    "#1c2333",
    "border":  "#2d3a52",
    "primary": "#1a8fff",
    "accent":  "#00d4ff",
    "teal":    "#00bcd4",
    "warn":    "#f59e0b",
    "danger":  "#ef4444",
    "good":    "#22c55e",
    "text":    "#f0f4ff",
    "muted":   "#8899bb",
}

RISK_COLORS = {
    "Critical": "#ef4444",
    "High":     "#f59e0b",
    "Moderate": "#1a8fff",
    "Low":      "#22c55e",
    "Unknown":  "#64748b",
}
RISK_EMOJI = {
    "Critical": "🔴",
    "High":     "🟠",
    "Moderate": "🟡",
    "Low":      "🟢",
    "Unknown":  "⚪",
}

# ================================================================
# ALTAIR THEME
# ================================================================
def _altair_theme():
    return {
        "config": {
            "view":       {"stroke": "transparent"},
            "background": THEME["bg"],
            "axis": {
                "labelColor": THEME["text"],
                "titleColor": THEME["text"],
                "gridColor":  THEME["border"],
            },
            "legend": {
                "labelColor": THEME["text"],
                "titleColor": THEME["text"],
            },
            "title":  {"color": THEME["text"]},
            "range":  {
                "category": [
                    THEME["primary"], THEME["accent"],
                    THEME["good"],    THEME["warn"],
                    THEME["danger"],
                ]
            },
        }
    }

alt.themes.register("vitalview", _altair_theme)
alt.themes.enable("vitalview")

# ================================================================
# CSS
# ================================================================
def inject_css():
    T = THEME
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&family=Mulish:wght@300;400;500;600&display=swap');

    html, body, [class*="css"] {{ font-family: 'Mulish', sans-serif !important; }}

    /* ── Force sidebar always visible ── */
    section[data-testid="stSidebar"] {{
        display: block !important;
        visibility: visible !important;
        opacity: 1 !important;
        transform: none !important;
        min-width: 280px !important;
        max-width: 320px !important;
        position: relative !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="false"] {{
        display: block !important;
        min-width: 280px !important;
    }}
    /* Hide the collapse arrow button */
    button[data-testid="collapsedControl"] {{
        display: none !important;
    }}
    [data-testid="stSidebarCollapseButton"] {{
        display: none !important;
    }}
    .stApp {{
        background: radial-gradient(ellipse at 15% 20%, #0d2040 0%, {T["bg"]} 45%),
                    radial-gradient(ellipse at 85% 80%, #001a35 0%, {T["bg"]} 50%) !important;
        background-color: {T["bg"]} !important;
        color:{T["text"]} !important;
    }}
    /* gradient flares */
    .stApp::before {{
        content:''; position:fixed; top:-200px; left:-200px;
        width:600px; height:600px; border-radius:50%;
        background:radial-gradient(circle, #1a4fff18 0%, transparent 70%);
        pointer-events:none; z-index:0;
    }}
    .stApp::after {{
        content:''; position:fixed; bottom:-150px; right:-150px;
        width:500px; height:500px; border-radius:50%;
        background:radial-gradient(circle, #00d4ff12 0%, transparent 70%);
        pointer-events:none; z-index:0;
    }}
    #MainMenu, footer, header {{ visibility:hidden; }}
    [data-testid="stSidebar"] {{
        background:{T["card"]} !important;
        border-right:1px solid {T["border"]} !important;
    }}
    /* Native sidebar toggle — let Streamlit manage position/visibility */
    [data-testid="collapsedControl"] button,
    [data-testid="stSidebarCollapsedControl"] button {{
        background: linear-gradient(180deg, {T["primary"]} 0%, {T["accent"]} 100%) !important;
        border-radius: 0 8px 8px 0 !important;
        color: white !important;
        border: none !important;
        box-shadow: 2px 0 12px rgba(0,180,255,0.3) !important;
    }}
    [data-testid="collapsedControl"] button svg,
    [data-testid="stSidebarCollapsedControl"] button svg {{
        stroke: white !important;
        fill: none !important;
    }}
    section[data-testid="stSidebar"] > div {{ overflow-y:auto !important; }}

    .block-container {{ padding:1.5rem 2.5rem 3rem !important; max-width:1320px !important; }}

    /* ── navbar ── */
    .vv-nav {{
        display:flex; align-items:center; justify-content:space-between;
        padding:0.75rem 0 1.25rem 0;
        border-bottom:1px solid {T["border"]}; margin-bottom:1.75rem;
    }}
    .vv-nav-left {{ display:flex; align-items:center; gap:0.75rem; }}
    .vv-wordmark {{
        font-family:'Syne',sans-serif; font-size:1.35rem; font-weight:800;
        letter-spacing:-0.03em; color:{T["text"]};
    }}
    .vv-wordmark em {{ color:{T["accent"]}; font-style:normal; }}
    .vv-badge {{
        font-size:0.62rem; font-weight:700; text-transform:uppercase;
        letter-spacing:0.08em; padding:0.18rem 0.6rem;
        border-radius:999px; border:1px solid;
    }}

    /* ── tabs ── */
    .stTabs [data-baseweb="tab-list"] {{
        background:transparent !important; gap:0 !important;
        border-bottom:1px solid {T["border"]} !important;
    }}
    .stTabs [data-baseweb="tab"] {{
        background:transparent !important; border:none !important;
        color:{T["muted"]} !important; font-family:'Mulish',sans-serif !important;
        font-size:0.85rem !important; font-weight:600 !important;
        padding:0.6rem 1.1rem !important; border-radius:0 !important;
        transition:color .15s !important;
    }}
    .stTabs [data-baseweb="tab"]:hover {{ color:{T["text"]} !important; }}
    .stTabs [aria-selected="true"] {{
        color:{T["accent"]} !important;
        border-bottom:2px solid {T["accent"]} !important;
    }}
    .stTabs [data-baseweb="tab-panel"] {{ padding:1.5rem 0 0 0 !important; }}

    /* ── cards ── */
    .vv-card {{
        background:{T["card"]}; border:1px solid {T["border"]};
        border-radius:10px; padding:1.25rem 1.5rem;
        transition:border-color .2s;
    }}
    .vv-card:hover {{ border-color:{T["primary"]}55; }}

    /* ── metric cards ── */
    .vv-metric {{
        background:{T["card"]}; border:1px solid {T["border"]};
        border-radius:10px; padding:1rem 1.25rem;
    }}
    .vv-metric-label {{
        font-size:0.68rem; font-weight:700; color:{T["muted"]};
        text-transform:uppercase; letter-spacing:0.08em; margin-bottom:0.3rem;
    }}
    .vv-metric-value {{
        font-family:'JetBrains Mono',monospace;
        font-size:1.7rem; font-weight:500; color:{T["text"]};
        letter-spacing:-0.02em;
    }}
    .vv-metric-sub {{ font-size:0.73rem; color:{T["muted"]}; margin-top:0.18rem; }}
    .good {{ color:{T["good"]} !important; }}
    .warn {{ color:{T["warn"]} !important; }}
    .bad  {{ color:{T["danger"]} !important; }}

    /* ── section headers ── */
    .vv-section {{
        font-family:'Syne',sans-serif; font-size:0.63rem; font-weight:700;
        color:{T["primary"]}; text-transform:uppercase; letter-spacing:0.12em;
        margin:2rem 0 1rem 0; display:flex; align-items:center; gap:0.6rem;
    }}
    .vv-section::after {{
        content:''; flex:1; height:1px; background:{T["border"]};
    }}

    /* ── upload zone ── */
    .vv-uploader [data-testid="stFileUploader"] {{
        background:{T["card"]} !important;
        border:2px dashed {T["border"]} !important;
        border-radius:12px !important; padding:1.5rem !important;
        transition:border-color .2s !important;
    }}
    .vv-uploader [data-testid="stFileUploader"]:hover {{
        border-color:{T["primary"]} !important;
    }}

    /* ── widgets ── */
    .stSelectbox > div > div,
    .stMultiSelect > div > div,
    .stTextInput > div > div {{
        background:#ffffff !important; border-color:#c8d6f0 !important;
        color:#1a2540 !important; border-radius:8px !important;
        box-shadow:0 1px 3px rgba(0,0,0,0.15) !important;
    }}
    .stTextInput input {{
        color:#1a2540 !important; background:#ffffff !important;
    }}
    label, .stSelectbox label, .stTextInput label, .stSlider label,
    .stMultiSelect label, .stNumberInput label, .stTextArea label {{
        color:{T["muted"]} !important; font-size:0.78rem !important;
        font-weight:600 !important;
    }}
    .stButton > button {{
        background:{T["primary"]} !important; color:#fff !important;
        border:none !important; border-radius:8px !important;
        font-family:'Mulish',sans-serif !important; font-weight:600 !important;
        padding:0.45rem 1.1rem !important; transition:opacity .15s !important;
    }}
    .stButton > button:hover {{ opacity:0.88 !important; }}
    .stDownloadButton > button {{
        background:transparent !important; color:{T["primary"]} !important;
        border:1px solid {T["primary"]} !important; border-radius:8px !important;
        font-family:'Mulish',sans-serif !important; font-weight:600 !important;
    }}
    div[data-testid="stMetric"] {{
        background:{T["card"]}; border:1px solid {T["border"]};
        border-radius:10px; padding:1rem 1.25rem;
    }}
    div[data-testid="stMetricLabel"] {{
        color:{T["muted"]} !important; font-size:0.75rem !important;
    }}
    div[data-testid="stMetricValue"] {{
        color:{T["text"]} !important;
        font-family:'JetBrains Mono',monospace !important;
    }}
    .stDataFrame {{
        border:1px solid {T["border"]} !important;
        border-radius:10px !important; overflow:hidden;
    }}
    .stAlert {{ border-radius:8px !important; }}
    textarea {{
        background:{T["card"]} !important; color:{T["text"]} !important;
        border-color:{T["border"]} !important; border-radius:8px !important;
    }}

    /* ── empty state ── */
    .vv-empty {{
        text-align:center; padding:4rem 2rem;
        background:{T["card"]}; border:1px dashed {T["border"]};
        border-radius:12px; margin-top:1rem;
    }}
    .vv-empty-icon  {{ font-size:2.5rem; margin-bottom:1rem; }}
    .vv-empty-title {{ color:{T["text"]}; font-size:1.05rem; font-weight:600; margin-bottom:0.4rem; }}
    .vv-empty-body  {{ color:{T["muted"]}; font-size:0.875rem; }}

    /* ── auth page ── */
    .vv-auth-wrap {{
        max-width:460px; margin:1.5rem auto;
        background:rgba(30,42,70,0.92);
        backdrop-filter:blur(16px);
        border:1px solid {T["border"]};
        border-radius:16px; padding:2.5rem;
        box-shadow:0 20px 60px rgba(0,0,0,0.4), 0 0 0 1px rgba(79,142,247,0.1);
    }}
    .vv-auth-title {{
        font-family:'Syne',sans-serif; font-size:1.1rem; font-weight:700;
        color:{T["text"]}; margin-bottom:1rem;
    }}
    /* ── plan cards on signup ── */
    .vv-plan-card {{
        border:2px solid {T["border"]}; border-radius:12px;
        padding:1rem; cursor:pointer; transition:all .2s;
        background:{T["card"]};
    }}
    .vv-plan-card:hover {{ border-color:{T["primary"]}; transform:translateY(-2px); }}
    .vv-plan-card.selected {{ border-color:{T["accent"]}; background:rgba(0,212,255,0.06); }}
    .vv-plan-name {{ font-family:Syne,sans-serif; font-weight:700; font-size:1rem; }}
    .vv-plan-price {{ font-family:'JetBrains Mono',monospace; font-size:1.4rem;
                      font-weight:500; margin:0.25rem 0; }}
    .vv-plan-feature {{ font-size:0.75rem; color:{T["muted"]}; line-height:1.8; }}

    /* ── disclaimer banner ── */
    .vv-disclaimer {{
        background:#0a1628; border:1px solid {T["primary"]}44;
        border-left:3px solid {T["primary"]}; border-radius:8px;
        padding:0.85rem 1rem; margin-bottom:1.5rem;
        font-size:0.8rem; color:{T["muted"]}; line-height:1.5;
    }}

    @media (max-width:768px) {{
        .block-container {{ padding:1rem 1rem 2rem !important; }}
        .vv-metric-value {{ font-size:1.3rem !important; }}
    }}
    </style>
    """, unsafe_allow_html=True)

# ================================================================
# UI HELPERS
# ================================================================
def navbar(user_name="", plan="free"):
    T = THEME
    colors = {
        "free":       T["muted"],
        "pro":        T["primary"],
        "enterprise": T["accent"],
    }
    c = colors.get(plan, T["muted"])
    name_html = (
        f'''<span style="font-size:0.85rem;color:{T["muted"]};">{user_name}</span>'''
        if user_name else ""
    )
    st.markdown(f"""
    <div class="vv-nav">
        <div class="vv-nav-left">
            {logo_img(52)}
            <div>
                <div class="vv-wordmark">Vital<em>View</em></div>
                <div style="font-size:0.68rem;color:{T["muted"]};font-weight:500;
                            letter-spacing:0.04em;margin-top:1px;">
                    Community Health Intelligence, Built for Action
                </div>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:0.75rem;">
            {name_html}
            <span class="vv-badge"
                  style="color:{c};border-color:{c}22;background:{c}18;">
                {plan.upper()}
            </span>
        </div>
    </div>""", unsafe_allow_html=True)


def section(label):
    st.markdown(f'''<div class="vv-section">{label}</div>''', unsafe_allow_html=True)


def metric_card(label, value, sub="", sub_class=""):
    sub_html = (
        f'''<div class="vv-metric-sub {sub_class}">{sub}</div>'''
        if sub else ""
    )
    st.markdown(f"""
    <div class="vv-metric">
        <div class="vv-metric-label">{label}</div>
        <div class="vv-metric-value">{value}</div>
        {sub_html}
    </div>""", unsafe_allow_html=True)


def empty_state(icon, title, body):
    st.markdown(f"""
    <div class="vv-empty">
        <div class="vv-empty-icon">{icon}</div>
        <div class="vv-empty-title">{title}</div>
        <div class="vv-empty-body">{body}</div>
    </div>""", unsafe_allow_html=True)


def disclaimer_banner():
    st.markdown("""
    <div class="vv-disclaimer">
        🔒 <strong>Data Privacy:</strong>
        VitalView does not store, transmit, or retain any files you upload.
        All analysis runs in your browser session only and is cleared when you close the tab.
        Do not upload data containing individual patient records or PHI.
    </div>""", unsafe_allow_html=True)

# ================================================================
# DATABASE — Supabase (PostgreSQL) backend
# Falls back to SQLite if Supabase credentials are not set
# ================================================================
def _get_supabase_creds():
    """Return (url, key) from secrets or env, or (None, None)."""
    try:
        url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
        key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", ""))
    except Exception:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
    return (url or "").strip(), (key or "").strip()


def _use_supabase():
    url, key = _get_supabase_creds()
    return bool(url and key)


def _sb():
    """Return a supabase client, or raise if not available."""
    from supabase import create_client
    url, key = _get_supabase_creds()
    return create_client(url, key)


# ── SQLite fallback ───────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_sqlite():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                email      TEXT    NOT NULL UNIQUE,
                password   TEXT    NOT NULL,
                plan       TEXT    NOT NULL DEFAULT 'free',
                approved   INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                email   TEXT,
                code    TEXT,
                expires INTEGER
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT,
                action     TEXT,
                detail     TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.commit()


def _seed_accounts():
    """Seed demo/admin accounts — works for both Supabase and SQLite."""
    _demo_accounts = [
        ("Demo User",     "demo@vitalview.com",  "demo1234",    "free"),
        ("Pro Tester",    "pro@vitalview.com",   "pro12345",    "pro"),
        ("Christopher",   "admin@vitalview.com", "VVadmin2024!", "admin"),
        ("Florida Pilot", "pilot@vitalview.com", "pilot2024!",  "pro"),
    ]
    for _name, _email, _pwd, _plan in _demo_accounts:
        try:
            if _use_supabase():
                sb = _sb()
                existing = sb.table("users").select("id").eq("email", _email).execute()
                if not existing.data:
                    sb.table("users").insert({
                        "name": _name, "email": _email,
                        "password": hash_pw(_pwd), "plan": _plan, "approved": True
                    }).execute()
            else:
                with get_conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO users(name,email,password,plan) VALUES(?,?,?,?)",
                        (_name, _email, hash_pw(_pwd), _plan),
                    )
                    conn.commit()
        except Exception:
            pass


def init_db():
    if _use_supabase():
        # Tables must exist in Supabase — created via SQL editor
        # Just seed the accounts
        try:
            _seed_accounts()
            logger.info("Supabase backend active")
        except Exception as e:
            logger.warning("Supabase seed failed, falling back to SQLite: %s", e)
            _init_sqlite()
            _seed_accounts()
    else:
        _init_sqlite()
        _seed_accounts()


def audit(email, action, detail=""):
    try:
        if _use_supabase():
            _sb().table("audit_log").insert({
                "email": email, "action": action, "detail": detail
            }).execute()
        else:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO audit_log(email,action,detail) VALUES(?,?,?)",
                    (email, action, detail),
                )
                conn.commit()
    except Exception as e:
        logger.warning("Audit log failed: %s", e)

# ================================================================
# AUTH
# ================================================================
def hash_pw(password):
    if HAS_BCRYPT:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def check_pw(plain, hashed):
    if HAS_BCRYPT:
        try:
            return bcrypt.checkpw(plain.encode(), hashed.encode())
        except Exception:
            return False
    import hashlib
    return hashlib.sha256(plain.encode()).hexdigest() == hashed


def valid_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))


def _rk(email):
    return f"_attempts_{email.strip().lower()}"


def is_locked(email):
    attempts, since = st.session_state.get(_rk(email), (0, time.time()))
    if attempts >= MAX_LOGIN_ATTEMPTS:
        if time.time() - since < LOCKOUT_SECONDS:
            return True
        st.session_state[_rk(email)] = (0, time.time())
    return False


def record_fail(email):
    attempts, since = st.session_state.get(_rk(email), (0, time.time()))
    st.session_state[_rk(email)] = (attempts + 1, since if attempts > 0 else time.time())


def clear_attempts(email):
    st.session_state.pop(_rk(email), None)


def add_user(name, email, password, plan="free"):
    name, email = name.strip(), email.strip().lower()
    if not (name and email and password):
        return False, "All fields are required."
    if not valid_email(email):
        return False, "Invalid email format."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if plan not in VALID_PLANS:
        return False, f"Invalid plan: {plan}"
    try:
        if _use_supabase():
            sb = _sb()
            existing = sb.table("users").select("id").eq("email", email).execute()
            if existing.data:
                return False, "An account with that email already exists."
            sb.table("users").insert({
                "name": name, "email": email,
                "password": hash_pw(password), "plan": plan, "approved": True
            }).execute()
        else:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO users(name,email,password,plan) VALUES(?,?,?,?)",
                    (name, email, hash_pw(password), plan),
                )
                conn.commit()
        audit(email, "signup", plan)
        return True, "Account created successfully."
    except Exception as e:
        err = str(e)
        if "duplicate" in err.lower() or "unique" in err.lower():
            return False, "An account with that email already exists."
        return False, f"Error: {e}"


def get_user(email):
    email = email.strip().lower()
    try:
        if _use_supabase():
            res = _sb().table("users").select("*").eq("email", email).execute()
            return res.data[0] if res.data else None
        else:
            with get_conn() as conn:
                return conn.execute(
                    "SELECT * FROM users WHERE email=?", (email,)
                ).fetchone()
    except Exception as e:
        logger.warning("get_user error: %s", e)
        return None


def verify_login(email, password):
    email = email.strip().lower()
    if is_locked(email):
        k = _rk(email)
        since = st.session_state.get(k, (0, time.time()))[1]
        remaining = max(0, int(LOCKOUT_SECONDS - (time.time() - since)))
        st.error(f"⛔ Account temporarily locked. Try again in {remaining}s.")
        return None
    user = get_user(email)
    if user and check_pw(password, user["password"]):
        clear_attempts(email)
        audit(email, "login")
        return user
    record_fail(email)
    return None


def start_reset(email):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    email = email.strip().lower()
    if not get_user(email):
        return False, "No account found with that email."
    code = secrets.token_hex(3).upper()
    exp  = int(time.time()) + 900
    if _use_supabase():
        sb = _sb()
        sb.table("password_resets").delete().eq("email", email).execute()
        sb.table("password_resets").insert({
            "email": email, "code": code, "expires": exp
        }).execute()
    else:
        with get_conn() as conn:
            conn.execute("DELETE FROM password_resets WHERE email=?", (email,))
            conn.execute(
                "INSERT INTO password_resets(email,code,expires) VALUES(?,?,?)",
                (email, code, exp),
            )
            conn.commit()

    # Send reset email via Gmail SMTP
    try:
        gmail_user = st.secrets.get("GMAIL_USER", os.getenv("GMAIL_USER", "vitalviewchi@gmail.com"))
        gmail_pwd  = st.secrets.get("GMAIL_APP_PASSWORD", os.getenv("GMAIL_APP_PASSWORD", ""))
    except Exception:
        gmail_user = os.getenv("GMAIL_USER", "vitalviewchi@gmail.com")
        gmail_pwd  = os.getenv("GMAIL_APP_PASSWORD", "")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "VitalView — Your Password Reset Code"
        msg["From"]    = f"VitalView <{gmail_user}>"
        msg["To"]      = email

        html_body = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;
                    background:#111827;color:#f0f4ff;border-radius:12px;
                    padding:2rem;border:1px solid #2d3a52;">
            <div style="font-size:1.4rem;font-weight:800;color:#1a8fff;
                        margin-bottom:0.5rem;">VitalView</div>
            <div style="font-size:1rem;margin-bottom:1.5rem;color:#8899bb;">
                Community Health Intelligence Platform
            </div>
            <p>You requested a password reset. Use the code below —
               it expires in <b>15 minutes</b>.</p>
            <div style="font-size:2rem;font-weight:800;letter-spacing:0.25em;
                        text-align:center;padding:1.25rem;margin:1.5rem 0;
                        background:#1c2333;border-radius:10px;
                        border:2px solid #1a8fff;color:#00d4ff;">
                {code}
            </div>
            <p style="font-size:0.8rem;color:#8899bb;">
                If you didn't request this, you can safely ignore this email.
            </p>
        </div>"""

        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pwd)
            server.sendmail(gmail_user, email, msg.as_string())

        return True, "Reset code sent! Check your email (and spam folder)."
    except Exception as e:
        logger.error("Email send failed: %s", e)
        return False, "Could not send reset email. Please contact support@vitalview.health."


def finish_reset(email, code, newpwd):
    email, code = email.strip().lower(), code.strip().upper()
    if not (email and code and newpwd):
        return False, "All fields are required."
    try:
        if _use_supabase():
            sb  = _sb()
            res = sb.table("password_resets").select("code,expires").eq("email", email).execute()
            if not res.data:
                return False, "No reset request found for this email."
            row = res.data[0]
            if code != row["code"]:
                return False, "Invalid reset code."
            if time.time() > row["expires"]:
                return False, "Reset code has expired. Please request a new one."
            sb.table("users").update({"password": hash_pw(newpwd)}).eq("email", email).execute()
            sb.table("password_resets").delete().eq("email", email).execute()
        else:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT code,expires FROM password_resets WHERE email=?", (email,)
                ).fetchone()
                if not row:
                    return False, "No reset request found for this email."
                if code != row["code"]:
                    return False, "Invalid reset code."
                if time.time() > row["expires"]:
                    return False, "Reset code has expired. Please request a new one."
                conn.execute("UPDATE users SET password=? WHERE email=?", (hash_pw(newpwd), email))
                conn.execute("DELETE FROM password_resets WHERE email=?", (email,))
                conn.commit()
        audit(email, "password_reset")
        return True, "Password updated. Please log in with your new password."
    except Exception as e:
        return False, f"Reset failed: {e}"

# ================================================================
# DATA HELPERS
# ================================================================
SUPPORTED_EXT = {".csv", ".xlsx", ".xls", ".xlsm"}


def load_file(file):
    suffix = Path(file.name).suffix.lower()
    if suffix not in SUPPORTED_EXT:
        raise ValueError(
            f"Unsupported file type '{suffix}'. Please upload a CSV or Excel file."
        )
    try:
        file.seek(0)
    except Exception:
        pass
    if suffix == ".csv":
        return pd.read_csv(file)
    return pd.read_excel(file)


def get_numeric_cols(df):
    if df is None or df.empty:
        return []
    SKIP = {"fips", "geoid", "id", "zip", "zipcode", "zcta"}
    out = []
    for c in df.select_dtypes(include="number").columns:
        if c.strip().lower() in SKIP:
            continue
        s = df[c]
        if s.notna().sum() == 0 or s.nunique(dropna=True) <= 1:
            continue
        out.append(c)
    return out


def enforce_schema(df):
    req = {"state", "county", "fips", "year", "indicator", "value", "unit"}
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    if not req.issubset(set(df.columns)):
        return pd.DataFrame()
    df["year"]  = pd.to_numeric(df["year"],  errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["year", "value"])
    for c in ("state", "county", "indicator", "unit"):
        df[c] = df[c].astype(str).str.strip()
    df["state"]  = df["state"].str.title()
    df["county"] = df["county"].str.title()
    # CRITICAL: FIPS must be zero-padded 5-char string for choropleth to match GeoJSON
    df["fips"] = (
        df["fips"].astype(str)
        .str.replace(r"\.0$", "", regex=True)   # drop .0 from float cast
        .str.strip()
        .str.zfill(5)
    )
    return df


def dashboard_ready(df):
    req = {"state", "county", "fips", "year", "indicator", "value", "unit"}
    return (
        df is not None
        and not df.empty
        and req.issubset(set(df.columns))
    )


def zscore(s):
    s   = pd.to_numeric(s, errors="coerce").astype(float)
    std = s.std(ddof=0) or 1.0
    return (s - s.mean()) / std


def safe_csv(df):
    def esc(x):
        if isinstance(x, str) and x and x[0] in "=+-@":
            return "'" + x
        return x
    try:
        return df.map(esc).to_csv(index=False).encode("utf-8")
    except AttributeError:
        return df.applymap(esc).to_csv(index=False).encode("utf-8")


def to_excel(df):
    if not HAS_XLSX:
        return b""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="VitalView")
    return buf.getvalue()


def to_pdf(text, title="VitalView Report"):
    if not HAS_PDF:
        return b""
    buf    = BytesIO()
    c      = rl_canvas.Canvas(buf, pagesize=letter)
    w, h   = letter
    margin = 0.75 * inch
    y      = h - margin
    c.setTitle(title)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, title)
    y -= 0.35 * inch
    c.setFont("Helvetica", 10)
    for raw in text.replace("\r", "").split("\n"):
        if not raw.strip():
            y -= 0.18 * inch
            continue
        for line in simpleSplit(raw, "Helvetica", 10, w - 2 * margin):
            if y < margin:
                c.showPage()
                y = h - margin
                c.setFont("Helvetica", 10)
            c.drawString(margin, y, line)
            y -= 0.16 * inch
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()

# ================================================================
# DEMO DATA
# ================================================================
def make_demo_data():
    counties = [
        ("Illinois", "Cook",   "17031"),
        ("Illinois", "Lake",   "17097"),
        ("Illinois", "Will",   "17197"),
        ("Illinois", "DuPage", "17043"),
    ]
    years = list(range(2019, 2025))
    base  = {
        "Cook":   {"Obesity (%)": 33, "PM2.5 (µg/m³)": 9.8, "Uninsured (%)": 11,
                   "No Car HH (%)": 18, "Food Desert (%)": 16.8},
        "Lake":   {"Obesity (%)": 31, "PM2.5 (µg/m³)": 9.1, "Uninsured (%)":  9,
                   "No Car HH (%)":  7, "Food Desert (%)": 10.2},
        "Will":   {"Obesity (%)": 32, "PM2.5 (µg/m³)": 9.4, "Uninsured (%)": 10,
                   "No Car HH (%)":  6, "Food Desert (%)": 12.1},
        "DuPage": {"Obesity (%)": 30, "PM2.5 (µg/m³)": 8.9, "Uninsured (%)":  8,
                   "No Car HH (%)":  5, "Food Desert (%)":  9.0},
    }
    rng  = np.random.default_rng(42)
    rows = []
    for state, county, fips in counties:
        for ind, val in base[county].items():
            val = float(val)
            for i, yr in enumerate(years):
                v = round(val + rng.normal(0, 0.4) + i * rng.uniform(-0.1, 0.15), 2)
                rows.append([state, county, str(fips).zfill(5), yr, ind, max(0, v), "percent"])

    # ── Florida counties ──────────────────────────────────────
    fl_counties = [
        ("Florida","Miami-Dade","12086"),("Florida","Broward","12011"),
        ("Florida","Palm Beach","12099"),("Florida","Hillsborough","12057"),
        ("Florida","Orange","12095"),("Florida","Pinellas","12103"),
        ("Florida","Duval","12031"),("Florida","Lee","12071"),
        ("Florida","Polk","12105"),("Florida","Brevard","12009"),
        ("Florida","Collier","12021"),("Florida","Sarasota","12115"),
        ("Florida","Volusia","12127"),("Florida","Pasco","12101"),
        ("Florida","Seminole","12117"),
    ]
    fl_base = {
        "Miami-Dade":  {"Obesity (%)":33.2,"Uninsured (%)":19.1,"Food Desert (%)":22.4,"PM2.5 (µg/m³)":8.9,"No Car HH (%)":21.3},
        "Broward":     {"Obesity (%)":30.1,"Uninsured (%)":16.2,"Food Desert (%)":15.8,"PM2.5 (µg/m³)":8.4,"No Car HH (%)":14.1},
        "Palm Beach":  {"Obesity (%)":28.4,"Uninsured (%)":14.8,"Food Desert (%)":13.2,"PM2.5 (µg/m³)":7.9,"No Car HH (%)":11.2},
        "Hillsborough":{"Obesity (%)":31.7,"Uninsured (%)":17.3,"Food Desert (%)":18.1,"PM2.5 (µg/m³)":8.2,"No Car HH (%)":13.8},
        "Orange":      {"Obesity (%)":29.8,"Uninsured (%)":18.5,"Food Desert (%)":16.9,"PM2.5 (µg/m³)":7.8,"No Car HH (%)":12.4},
        "Pinellas":    {"Obesity (%)":30.5,"Uninsured (%)":15.1,"Food Desert (%)":14.3,"PM2.5 (µg/m³)":7.6,"No Car HH (%)":10.9},
        "Duval":       {"Obesity (%)":32.1,"Uninsured (%)":16.8,"Food Desert (%)":19.2,"PM2.5 (µg/m³)":8.1,"No Car HH (%)":14.7},
        "Lee":         {"Obesity (%)":29.2,"Uninsured (%)":20.4,"Food Desert (%)":12.8,"PM2.5 (µg/m³)":7.4,"No Car HH (%)": 9.8},
        "Polk":        {"Obesity (%)":34.1,"Uninsured (%)":18.9,"Food Desert (%)":20.6,"PM2.5 (µg/m³)":8.3,"No Car HH (%)":11.6},
        "Brevard":     {"Obesity (%)":30.8,"Uninsured (%)":15.6,"Food Desert (%)":14.7,"PM2.5 (µg/m³)":7.7,"No Car HH (%)":10.2},
        "Collier":     {"Obesity (%)":27.3,"Uninsured (%)":22.1,"Food Desert (%)":11.9,"PM2.5 (µg/m³)":7.1,"No Car HH (%)": 8.4},
        "Sarasota":    {"Obesity (%)":28.9,"Uninsured (%)":13.7,"Food Desert (%)":12.1,"PM2.5 (µg/m³)":7.3,"No Car HH (%)": 9.1},
        "Volusia":     {"Obesity (%)":31.4,"Uninsured (%)":16.5,"Food Desert (%)":16.3,"PM2.5 (µg/m³)":7.8,"No Car HH (%)":12.7},
        "Pasco":       {"Obesity (%)":32.6,"Uninsured (%)":17.8,"Food Desert (%)":17.4,"PM2.5 (µg/m³)":8.0,"No Car HH (%)":11.3},
        "Seminole":    {"Obesity (%)":27.8,"Uninsured (%)":13.2,"Food Desert (%)":11.4,"PM2.5 (µg/m³)":7.5,"No Car HH (%)": 8.9},
    }
    for state, county, fips_raw in fl_counties:
        for ind, base_val in fl_base[county].items():
            for i, yr in enumerate(years):
                v = round(float(base_val) + rng.normal(0, 0.35) + i * rng.uniform(-0.08, 0.12), 2)
                rows.append([state, county, str(fips_raw).zfill(5), yr, ind, max(0, v), "percent"])

    df_out = pd.DataFrame(rows, columns=["state","county","fips","year","indicator","value","unit"])
    df_out["fips"] = df_out["fips"].astype(str).str.zfill(5)  # guarantee string format
    return df_out


@st.cache_data(show_spinner=False)
def get_demo_data():
    return make_demo_data()


@st.cache_data(show_spinner="Loading county map…")
def load_geojson():
    try:
        with open(COUNTY_GEOJSON_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("GeoJSON not found: %s", COUNTY_GEOJSON_PATH)
        return None

# ================================================================
# ANALYTICS
# ================================================================
def assign_risk_tier(series):
    s = pd.to_numeric(series, errors="coerce")
    if s.dropna().empty:
        return pd.Series(["Unknown"] * len(s), index=s.index)
    if s.nunique(dropna=True) <= 1:
        return pd.Series(["Moderate"] * len(s), index=s.index)
    q25, q50, q75 = s.quantile([0.25, 0.50, 0.75])
    def _tier(v):
        if pd.isna(v):  return "Unknown"
        if v <= q25:    return "Low"
        if v <= q50:    return "Moderate"
        if v <= q75:    return "High"
        return "Critical"
    return s.apply(_tier)


def build_equity_gap(df, location_col, need_col, service_col, date_col=None):
    work = df.copy()
    work["_need"]    = pd.to_numeric(work[need_col],    errors="coerce")
    work["_service"] = pd.to_numeric(work[service_col], errors="coerce")
    if date_col and date_col in work.columns:
        work["_year"] = pd.to_datetime(work[date_col], errors="coerce").dt.year
    else:
        work["_year"] = np.nan
    work["_events"] = work["_need"].fillna(0) + work["_service"].fillna(0)
    valid = work[["_need", "_service"]].notna().all(axis=1)
    grp   = work.groupby(location_col, dropna=False)
    agg   = grp[["_need", "_service", "_events"]].sum(min_count=1).reset_index()
    agg.rename(columns={"_need": "need_value", "_service": "service_value",
                        "_events": "n_events"}, inplace=True)
    comp = valid.groupby(work[location_col]).mean().reset_index(name="completeness")
    if work["_year"].notna().any():
        ny = grp["_year"].nunique().reset_index(name="n_years")
    else:
        ny = grp.size().reset_index(name="_dummy")
        ny["n_years"] = 1
        ny = ny[[location_col, "n_years"]]
    out = (agg
           .merge(comp, on=location_col, how="left")
           .merge(ny[[location_col, "n_years"]], on=location_col, how="left"))
    nl, ns_ = assign_risk_tier(out["need_value"]),    assign_risk_tier(out["need_value"])
    sl, ss_ = assign_risk_tier(out["service_value"]), assign_risk_tier(out["service_value"])
    out["need_level"]    = nl
    out["service_level"] = sl

    def gap_cat(row):
        n = str(row.get("need_level",    ""))
        s = str(row.get("service_level", ""))
        if n in ("High", "Critical") and s in ("Low", "Moderate"):
            return "Critical Equity Gap"
        if n in ("High", "Critical") and s in ("High", "Critical"):
            return "Burdened but Reached"
        if n in ("Low", "Moderate") and s in ("High", "Critical"):
            return "Potential Over-served"
        return "Lower Priority"

    out["equity_gap_category"] = out.apply(gap_cat, axis=1)

    def validity(comp_v, events, years):
        c = float(np.clip(float(comp_v  or 0), 0, 1))
        v = float(np.clip((float(events or 0) - 10) / 190, 0, 1))
        t = 0.3 if int(years or 0) <= 1 else (0.6 if int(years or 0) == 2 else 1.0)
        score = round(0.4 * c + 0.4 * v + 0.2 * t, 3)
        level = "High" if score >= 0.7 else ("Medium" if score >= 0.4 else "Low")
        return score, level

    vs, vl = zip(*[
        validity(r.completeness, r.n_events, r.n_years)
        for _, r in out.iterrows()
    ])
    out["validity_score"] = vs
    out["validity_level"] = vl

    cat_order = {
        "Critical Equity Gap": 0,
        "Burdened but Reached": 1,
        "Potential Over-served": 2,
        "Lower Priority": 3,
    }
    out["_rank"] = out["equity_gap_category"].map(cat_order).fillna(9)
    out = out.sort_values("_rank").drop(columns=["_rank"])
    out["priority_rank"] = range(1, len(out) + 1)
    return out


def build_zip_index(df, zip_col, indicators):
    work = df[[zip_col] + indicators].copy()
    for ind in indicators:
        work[ind] = pd.to_numeric(work[ind], errors="coerce")
    agg = work.groupby(zip_col, as_index=False)[indicators].mean().round(3)
    for ind in indicators:
        agg[f"{ind}_risk"] = assign_risk_tier(agg[ind])
    norm_scores = []
    for ind in indicators:
        s  = agg[ind]
        mn = s.min()
        mx = s.max()
        if mx > mn:
            norm_scores.append((s - mn) / (mx - mn))
        else:
            norm_scores.append(pd.Series([0.5] * len(s), index=s.index))
    if norm_scores:
        agg["composite_score"] = pd.concat(norm_scores, axis=1).mean(axis=1).round(3)
    else:
        agg["composite_score"] = 0.5
    agg["composite_risk"] = assign_risk_tier(agg["composite_score"])
    agg = agg.sort_values("composite_score", ascending=False).reset_index(drop=True)
    agg.insert(0, "rank", range(1, len(agg) + 1))
    return agg


def derive_pivot(df_latest):
    if df_latest is None or df_latest.empty:
        return pd.DataFrame()
    if "indicator" not in df_latest.columns or "value" not in df_latest.columns:
        return pd.DataFrame()
    idx = [c for c in ["state", "county", "fips"] if c in df_latest.columns]
    if not idx:
        return pd.DataFrame()
    try:
        piv = df_latest.pivot_table(
            index=idx, columns="indicator", values="value", aggfunc="mean"
        )
        piv.columns.name = None
        return piv.reset_index()
    except Exception:
        return pd.DataFrame()


def compute_priority(pivot, weights):
    if pivot is None or pivot.empty:
        return pd.DataFrame()
    z     = pivot.select_dtypes("number").apply(zscore)
    score = sum(w * z[c] for c, w in weights.items() if c in z.columns)
    out   = pivot.copy()
    out["E_Score"] = score if not isinstance(score, int) else 0
    return out.sort_values("E_Score", ascending=False)


def trend_forecast(df, time_col, value_col):
    if time_col not in df.columns or value_col not in df.columns:
        return None, None, None, None
    tmp = df[[time_col, value_col]].dropna()
    if tmp.empty or tmp[time_col].nunique() < 2:
        return None, None, None, None
    x = tmp[time_col]
    try:
        x     = pd.to_datetime(x)
        x_num = x.view("int64") / (1e9 * 60 * 60 * 24 * 365.25)
    except Exception:
        try:
            x_num = x.astype(float)
        except Exception:
            return None, None, None, None
    y = tmp[value_col].astype(float)
    if len(np.unique(x_num)) < 2:
        return None, None, None, None
    m, b      = np.polyfit(x_num, y, 1)
    last_t    = x_num.max()
    last_val  = float(y.loc[x_num.idxmax()])
    forecast  = m * (last_t + 1) + b
    avg       = y.mean()
    thr       = 0.01 * avg if avg else abs(m) * 0.1
    cls_label = "📈 Increasing" if m > thr else ("📉 Decreasing" if m < -thr else "➖ Stable")
    return float(m), last_val, float(forecast), cls_label

# ================================================================
# FLORIDA TEMPLATE
# ================================================================
def florida_template_csv():
    counties = [
        ("Alachua","12001"),("Baker","12003"),("Bay","12005"),("Bradford","12007"),
        ("Brevard","12009"),("Broward","12011"),("Calhoun","12013"),("Charlotte","12015"),
        ("Citrus","12017"),("Clay","12019"),("Collier","12021"),("Columbia","12023"),
        ("DeSoto","12027"),("Dixie","12029"),("Duval","12031"),("Escambia","12033"),
        ("Flagler","12035"),("Franklin","12037"),("Gadsden","12039"),("Gilchrist","12041"),
        ("Glades","12043"),("Gulf","12045"),("Hamilton","12047"),("Hardee","12049"),
        ("Hendry","12051"),("Hernando","12053"),("Highlands","12055"),("Hillsborough","12057"),
        ("Holmes","12059"),("Indian River","12061"),("Jackson","12063"),("Jefferson","12065"),
        ("Lafayette","12067"),("Lake","12069"),("Lee","12071"),("Leon","12073"),
        ("Levy","12075"),("Liberty","12077"),("Madison","12079"),("Manatee","12081"),
        ("Marion","12083"),("Martin","12085"),("Miami-Dade","12086"),("Monroe","12087"),
        ("Nassau","12089"),("Okaloosa","12091"),("Okeechobee","12093"),("Orange","12095"),
        ("Osceola","12097"),("Palm Beach","12099"),("Pasco","12101"),("Pinellas","12103"),
        ("Polk","12105"),("Putnam","12107"),("St. Johns","12109"),("St. Lucie","12111"),
        ("Santa Rosa","12113"),("Sarasota","12115"),("Seminole","12117"),("Sumter","12119"),
        ("Suwannee","12121"),("Taylor","12123"),("Union","12125"),("Volusia","12127"),
        ("Wakulla","12129"),("Walton","12131"),("Washington","12133"),
    ]
    rows = [
        {
            "county_name": c, "state": "Florida", "fips": f,
            "premature_death_rate": "", "poor_health_pct": "",
            "poor_physical_days": "", "poor_mental_days": "",
            "low_birthweight_pct": "", "adult_smoking_pct": "",
            "adult_obesity_pct": "", "physical_inactivity_pct": "",
            "excessive_drinking_pct": "", "uninsured_pct": "",
            "primary_care_ratio": "", "mental_health_provider_ratio": "",
            "preventable_hosp_rate": "", "high_school_grad_pct": "",
            "some_college_pct": "", "unemployment_pct": "",
            "children_poverty_pct": "", "income_inequality_ratio": "",
            "median_household_income": "", "air_pollution_avg": "",
            "severe_housing_pct": "", "long_commute_pct": "",
            "population": "", "pct_below_18": "", "pct_65_plus": "",
            "pct_black": "", "pct_hispanic": "", "pct_white": "", "pct_rural": "",
        }
        for c, f in counties
    ]
    return pd.DataFrame(rows).to_csv(index=False)

# ================================================================
# TAB — DASHBOARD
# ================================================================
def tab_dashboard(df, features):
    disclaimer_banner()
    section("Data Snapshot")
    if not dashboard_ready(df):
        empty_state(
            "📊", "No data loaded",
            "Enable Demo Mode in the sidebar, or upload a file via the Upload tab.",
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Total Rows", f"{len(df):,}")
    with c2:
        cnt = df["county"].nunique() if "county" in df.columns else 0
        metric_card("Counties", f"{cnt:,}")
    with c3:
        ind = df["indicator"].nunique() if "indicator" in df.columns else 0
        metric_card("Indicators", f"{ind:,}")
    with c4:
        if "year" in df.columns:
            ymin, ymax = int(df["year"].min()), int(df["year"].max())
            metric_card("Year Range", f"{ymin}–{ymax}")
        else:
            metric_card("Year Range", "—")

    section("Data Quality")
    issues = []
    missing = int(df.isna().sum().sum())
    if missing:
        issues.append(f"{missing:,} missing values detected across all cells.")
    dups = int(df.duplicated().sum())
    if dups:
        issues.append(f"{dups:,} duplicate rows found.")
    if "value" in df.columns and (df["value"] < 0).any():
        issues.append("Negative indicator values found — verify these are intentional.")
    if "year" in df.columns and df["year"].max() - df["year"].min() < 2:
        issues.append("Year range is narrow (< 2 years) — trend lines may not be meaningful.")
    if not issues:
        st.success("✅ No major data quality issues detected.")
    else:
        for iss in issues:
            st.warning(f"• {iss}")

    section("Indicator Summary")
    if "indicator" in df.columns and "value" in df.columns:
        summary = (
            df.groupby("indicator")["value"]
            .agg(["mean", "min", "max", "count"])
            .round(2)
            .reset_index()
            .rename(columns={
                "indicator": "Indicator",
                "mean": "Mean", "min": "Min",
                "max": "Max", "count": "Records",
            })
        )
        st.dataframe(summary, use_container_width=True, height=300)

    section("Download Cleaned Data")
    def _clean(d):
        d = d.drop_duplicates()
        if "year"  in d.columns: d = d[d["year"].notna()]
        if "value" in d.columns: d = d[(d["value"].notna()) & (d["value"] >= 0)]
        return d

    cleaned = _clean(df.copy())
    st.caption(f"Cleaned: {len(cleaned):,} rows after removing duplicates, nulls, and negatives.")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇ Download CSV", data=safe_csv(cleaned),
            file_name="vitalview_clean.csv", mime="text/csv",
        )
    with col2:
        if features.get("exports") and HAS_XLSX:
            xb = to_excel(cleaned)
            if xb:
                st.download_button(
                    "⬇ Download Excel", data=xb,
                    file_name="vitalview_clean.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        elif not features.get("exports"):
            st.info("Excel export is available on the Pro plan.")

# ================================================================
# TAB — UPLOAD
# ================================================================
def tab_upload():
    disclaimer_banner()
    section("Upload Your Data")
    st.markdown(
        f"<p style='color:{THEME['muted']};font-size:0.9rem;margin-bottom:1.5rem;'>"
        "Upload a CSV or Excel file with county-level health indicators. "
        "Your data will be available across all tabs once loaded.</p>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="vv-uploader">', unsafe_allow_html=True)
    uploaded = st.file_uploader(
        "Drop your file here or click to browse",
        type=["csv", "xlsx", "xls"],
        help="CSV or Excel, max 50 MB",
        key="main_uploader",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    if uploaded is None:
        st.markdown(
            f"<p style='color:{THEME['muted']};font-size:0.8rem;margin-top:0.75rem;'>"
            "💡 Need a starting point? Download the Florida county template below.</p>",
            unsafe_allow_html=True,
        )
        col1, _ = st.columns([1, 4])
        with col1:
            st.download_button(
                "⬇ Florida Template",
                data=florida_template_csv(),
                file_name="florida_county_template.csv",
                mime="text/csv",
            )
        return

    try:
        df = load_file(uploaded)
    except ValueError as e:
        st.error(f"⚠️ Could not read file: {e}")
        return
    except Exception:
        st.error("⚠️ Something went wrong reading that file. Make sure it's a valid CSV or Excel file and try again.")
        return

    if df is None or df.empty:
        st.warning("File appears to be empty.")
        return

    st.session_state["raw_df"]      = df
    st.session_state["upload_name"] = uploaded.name
    # Clear cached radio so map tab auto-switches to uploaded file
    for _k in ("map_source", "map_indicator", "map_type"):
        st.session_state.pop(_k, None)

    section("File Summary")
    numeric_cols = get_numeric_cols(df)
    miss_pct     = round(df.isnull().mean().mean() * 100, 1)
    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Rows",         f"{len(df):,}")
    with c2: metric_card("Columns",      str(len(df.columns)))
    with c3: metric_card("Numeric Cols", str(len(numeric_cols)))
    with c4:
        sc = "good" if miss_pct < 5 else ("warn" if miss_pct < 20 else "bad")
        metric_card("Missing", f"{miss_pct}%", sub_class=sc)

    section("Data Preview")
    col_l, col_r = st.columns([3, 1])
    with col_r:
        n_rows = st.selectbox("Rows", [10, 25, 50, 100], label_visibility="collapsed")
    with col_l:
        search = st.text_input(
            "🔍 Filter columns", placeholder="Type to filter…",
            label_visibility="collapsed",
        )
    display = df.head(n_rows)
    if search:
        matched = [c for c in df.columns if search.lower() in c.lower()]
        display = df[matched].head(n_rows) if matched else display
    st.dataframe(display, use_container_width=True, height=300)

    schema_df = enforce_schema(df)
    if dashboard_ready(schema_df):
        st.session_state["df"] = schema_df
        st.success(
            "✅ VitalView schema detected — Dashboard, Map, and Reports tabs are now ready."
        )
    else:
        st.info(
            "ℹ️ File loaded for ZIP Heatmap and Equity Scanner tabs. "
            "For Dashboard/Map/Reports, columns needed: "
            "`state, county, fips, year, indicator, value, unit`"
        )

# ================================================================
# TAB — EQUITY GAP SCANNER
# ================================================================
def tab_equity_scanner():
    disclaimer_banner()
    section("Equity Gap Scanner")
    st.markdown(
        f"<p style='color:{THEME['muted']};font-size:0.9rem;margin-bottom:1.5rem;'>"
        "Upload a file with a <b>need metric</b> (burden, cases, ED visits) and a "
        "<b>service metric</b> (contacts, visits, reach) to identify where gaps "
        "are largest.</p>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="vv-uploader">', unsafe_allow_html=True)
    gap_file = st.file_uploader(
        "Upload file for Equity Gap scan",
        type=["csv", "xlsx", "xls"],
        key="gap_uploader",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    if gap_file is None:
        empty_state(
            "⚖️", "No file uploaded",
            "Upload a file above. You need a location column, a need column, "
            "and a service column.",
        )
        return

    try:
        df_raw = load_file(gap_file)
    except ValueError as e:
        st.error(f"⚠️ Could not read file: {e}")
        return
    except Exception:
        st.error("⚠️ Something went wrong reading that file. Make sure it's a valid CSV or Excel file and try again.")
        return

    if df_raw is None or df_raw.empty:
        st.warning("File appears to be empty.")
        return

    cols     = df_raw.columns.tolist()
    num_cols = get_numeric_cols(df_raw)

    section("Configure Scanner")
    c1, c2 = st.columns(2)
    with c1:
        loc_candidates = [
            c for c in cols
            if any(k in c.lower() for k in
                   ["zip", "county", "tract", "neighborhood", "area",
                    "region", "city", "campus", "site"])
        ] or cols
        location_col = st.selectbox("Location column", loc_candidates, key="gap_loc")
    if len(num_cols) < 2:
        st.warning("Need at least 2 numeric columns.")
        return
    with c2:
        need_col = st.selectbox("Need metric (burden/cases)", num_cols, key="gap_need")

    c3, c4 = st.columns(2)
    with c3:
        svc_choices = [c for c in num_cols if c != need_col]
        service_col = st.selectbox(
            "Service metric (reach/contacts)", svc_choices, key="gap_svc"
        )
    with c4:
        date_candidates = ["(none)"] + [
            c for c in cols
            if any(k in c.lower() for k in ["date", "year", "month"])
        ]
        date_raw = st.selectbox(
            "Date/year column (optional)", date_candidates, key="gap_date"
        )
        date_col = None if date_raw == "(none)" else date_raw

    if st.button("🔍 Run Equity Gap Scan", type="primary", key="gap_run"):
        with st.spinner("Analysing equity gaps…"):
            result = build_equity_gap(
                df_raw, location_col, need_col, service_col, date_col
            )
        st.session_state["gap_result"]      = result
        st.session_state["gap_location_col"] = location_col
        st.success(f"Scan complete — {len(result)} locations analysed.")

    df_gap  = st.session_state.get("gap_result")
    if df_gap is None or df_gap.empty:
        return

    loc_col = st.session_state.get("gap_location_col", location_col)

    section("Equity Gap Summary")
    cats = df_gap["equity_gap_category"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Critical Gaps",      str(cats.get("Critical Equity Gap",    0)), sub_class="bad")
    with c2: metric_card("Burdened & Reached", str(cats.get("Burdened but Reached",   0)), sub_class="warn")
    with c3: metric_card("Over-served",        str(cats.get("Potential Over-served",  0)))
    with c4: metric_card("Lower Priority",     str(cats.get("Lower Priority",         0)), sub_class="good")

    section("Results Table")
    core = [
        c for c in [
            "priority_rank", loc_col, "need_level", "service_level",
            "equity_gap_category", "validity_level", "validity_score",
        ]
        if c in df_gap.columns
    ]
    st.dataframe(df_gap[core], use_container_width=True, height=320)

    section("Need vs Service Chart")
    if all(c in df_gap.columns for c in ["equity_gap_category"]):
        need_s = "need_value"   if "need_value"    in df_gap.columns else None
        svc_s  = "service_value" if "service_value" in df_gap.columns else None
        if need_s and svc_s:
            color_map = {
                "Critical Equity Gap":   THEME["danger"],
                "Burdened but Reached":  THEME["warn"],
                "Potential Over-served": THEME["primary"],
                "Lower Priority":        THEME["good"],
            }
            chart = (
                alt.Chart(df_gap)
                .mark_circle(size=90, opacity=0.85)
                .encode(
                    x=alt.X(f"{svc_s}:Q",  title="Service Value (reach)"),
                    y=alt.Y(f"{need_s}:Q", title="Need Value (burden)"),
                    color=alt.Color(
                        "equity_gap_category:N",
                        scale=alt.Scale(
                            domain=list(color_map.keys()),
                            range=list(color_map.values()),
                        ),
                        legend=alt.Legend(title="Gap Category"),
                    ),
                    tooltip=[
                        alt.Tooltip(f"{loc_col}:N",            title="Location"),
                        alt.Tooltip("need_level:N"),
                        alt.Tooltip("service_level:N"),
                        alt.Tooltip("equity_gap_category:N"),
                        alt.Tooltip("validity_level:N"),
                    ],
                )
                .properties(height=340)
            )
            st.altair_chart(chart, use_container_width=True)

    section("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇ Download CSV", data=safe_csv(df_gap),
            file_name="equity_gap_results.csv", mime="text/csv",
        )
    with col2:
        if HAS_XLSX:
            xb = to_excel(df_gap)
            if xb:
                st.download_button(
                    "⬇ Download Excel", data=xb,
                    file_name="equity_gap_results.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

# ================================================================
# TAB — MAP
# ================================================================
def _render_map(df_plot, fips_col, value_col, hover_cols=None, title=""):
    """
    Render a US county choropleth using Plotly's built-in FIPS scope.
    No local GeoJSON file required — boundaries come from plotly itself.
    fips_col must contain zero-padded 5-char strings like '17031'.
    """
    T = THEME
    try:
        hover_data = {c: True for c in (hover_cols or [])}
        hover_data[fips_col] = False   # hide raw FIPS from tooltip

        fig = px.choropleth(
            df_plot,
            locations=fips_col,
            locationmode="USA-states",   # fallback scope
            color=value_col,
            hover_data=hover_data,
            color_continuous_scale="Blues",
            labels={value_col: title or value_col},
            scope="usa",
        )

        # Override to county-level using geojson from plotly's cdn cache
        from urllib.request import urlopen
        import json as _json
        try:
            with urlopen(
                "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
                timeout=4
            ) as resp:
                counties_geo = _json.load(resp)
            fig = px.choropleth(
                df_plot,
                geojson=counties_geo,
                locations=fips_col,
                featureidkey="id",
                color=value_col,
                hover_data=hover_data,
                color_continuous_scale="Blues",
                labels={value_col: title or value_col},
            )
            fig.update_geos(fitbounds="locations", visible=False)
        except Exception:
            # No internet — fall back to state-level scope view
            fig.update_geos(scope="usa", visible=True,
                            showlakes=False, showland=True,
                            landcolor="#1c2333", bgcolor=T["bg"])

        fig.update_layout(
            margin={"r": 0, "t": 10, "l": 0, "b": 0},
            paper_bgcolor=T["bg"],
            plot_bgcolor=T["bg"],
            font_color=T["text"],
            height=520,
            coloraxis_colorbar=dict(
                bgcolor=T["card"],
                bordercolor=T["border"],
                tickfont=dict(color=T["text"]),
                title=dict(font=dict(color=T["text"])),
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
        return True
    except Exception as e:
        st.warning(f"Map render error: {e}")
        return False


def tab_map(df):
    section("County & ZIP Map")

    # ── Prominent data source switcher ───────────────────────
    T = THEME
    has_upload   = st.session_state.get("upload_name") is not None
    upload_fname = st.session_state.get("upload_name", "")
    use_demo_default = not has_upload or st.session_state.get("demo_mode", True)

    # Use session state key so switching persists without rerun
    if "map_data_choice" not in st.session_state:
        st.session_state["map_data_choice"] = "demo" if use_demo_default else "upload"

    # Card-style switcher
    ca, cb = st.columns(2)
    with ca:
        demo_selected = st.session_state["map_data_choice"] == "demo"
        demo_border   = T["accent"] if demo_selected else T["border"]
        demo_bg       = "rgba(0,212,255,0.07)" if demo_selected else T["card"]
        st.markdown(f"""
        <div style="border:2px solid {demo_border};border-radius:12px;
                    background:{demo_bg};padding:1rem 1.2rem;
                    transition:all .2s;cursor:pointer;">
            <div style="font-size:1.3rem;margin-bottom:0.3rem;">📊</div>
            <div style="font-family:Syne,sans-serif;font-weight:700;
                        color:{T["text"]};font-size:0.95rem;">Demo Data</div>
            <div style="font-size:0.75rem;color:{T["muted"]};margin-top:0.2rem;">
                Illinois + Florida counties (19 counties, 5 indicators)
            </div>
            {"<div style='margin-top:0.5rem;font-size:0.68rem;font-weight:700;color:"+T["accent"]+";text-transform:uppercase;letter-spacing:0.08em;'>✓ Active</div>" if demo_selected else ""}
        </div>""", unsafe_allow_html=True)
        if st.button("Use Demo Data", key="pick_demo", use_container_width=True):
            st.session_state["map_data_choice"] = "demo"
            st.rerun()

    with cb:
        up_selected = st.session_state["map_data_choice"] == "upload"
        up_border   = T["primary"] if up_selected else T["border"]
        up_bg       = "rgba(26,143,255,0.07)" if up_selected else T["card"]
        if has_upload:
            up_label = f"📂 {upload_fname}"
            up_sub   = f"{len(st.session_state.get('df', pd.DataFrame()))} rows loaded — ready to map"
            up_color = T["primary"]
        else:
            up_label = "📂 Your Uploaded File"
            up_sub   = "Upload a CSV via the sidebar first (state, county, fips, year, indicator, value, unit)"
            up_color = T["muted"]
        st.markdown(f"""
        <div style="border:2px solid {up_border};border-radius:12px;
                    background:{up_bg};padding:1rem 1.2rem;
                    transition:all .2s;{'cursor:pointer;' if has_upload else 'opacity:0.6;'}">
            <div style="font-size:1.3rem;margin-bottom:0.3rem;">📂</div>
            <div style="font-family:Syne,sans-serif;font-weight:700;
                        color:{T["text"]};font-size:0.95rem;">{upload_fname or "Your File"}</div>
            <div style="font-size:0.75rem;color:{T["muted"]};margin-top:0.2rem;">{up_sub}</div>
            {"<div style='margin-top:0.5rem;font-size:0.68rem;font-weight:700;color:"+T["primary"]+";text-transform:uppercase;letter-spacing:0.08em;'>✓ Active</div>" if up_selected else ""}
        </div>""", unsafe_allow_html=True)
        if st.button(
            "✅ Use This File" if has_upload else "⬆ Use Uploaded File",
            key="pick_upload",
            use_container_width=True,
        ):
            if has_upload:
                st.session_state["map_data_choice"] = "upload"
                st.session_state.pop("map_indicator", None)
                st.session_state.pop("map_type", None)
                st.rerun()
            else:
                st.info("📂 Upload a CSV first using the sidebar on the left, then click here.")

    st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)

    use_demo = st.session_state["map_data_choice"] == "demo"
    if use_demo:
        df_map = get_demo_data()
    else:
        uploaded_schema = st.session_state.get("df", pd.DataFrame())
        df_map = uploaded_schema if dashboard_ready(uploaded_schema) else st.session_state.get("raw_df", pd.DataFrame())

    if df_map is None or df_map.empty:
        empty_state("🗺️", "No data to map",
                    "Enable Demo Data above, or upload a CSV via the sidebar.")
        return

    # ── Detect what kind of file we have ─────────────────────
    is_schema = dashboard_ready(df_map)
    cols_lower = [c.lower() for c in df_map.columns]
    has_zip  = any(k in cols_lower for k in ["zip","zip_code","postal","zcta","zipcode"])
    has_fips = any("fips" in c for c in cols_lower)

    # ── What can we map? ──────────────────────────────────────
    map_type_opts = []
    if is_schema or has_fips:
        map_type_opts.append("🏛 County (by FIPS)")
    if has_zip:
        map_type_opts.append("📍 ZIP Code (by ZIP)")
    if not map_type_opts:
        st.warning(
            "Your file needs either a **`fips`** column (for county maps) "
            "or a **`zip_code`** column (for ZIP maps). "
            "Check the sidebar format guide."
        )
        section("Your Data (preview)")
        st.dataframe(df_map.head(30), use_container_width=True)
        return

    if len(map_type_opts) > 1:
        map_type = st.radio("Map level", map_type_opts, horizontal=True, key="map_type_radio")
    else:
        map_type = map_type_opts[0]
        st.info(f"Mapping at: **{map_type}**")

    # ── COUNTY MAP ────────────────────────────────────────────
    if "County" in map_type:
        if is_schema:
            latest    = int(df_map["year"].max()) if "year" in df_map.columns and not df_map["year"].isna().all() else None
            if latest is None:
                st.warning("⚠️ No valid year data found in this file.")
                return
            df_l      = df_map[df_map["year"] == latest]
            inds      = sorted(df_l["indicator"].dropna().unique().tolist())
            indicator = st.selectbox("Indicator to map", inds, key="map_indicator")
            df_wide   = (
                df_l[df_l["indicator"] == indicator]
                .groupby(["fips","county","state"], as_index=False)["value"].mean()
            )
            df_wide["fips"] = df_wide["fips"].astype(str).str.zfill(5)
        else:
            # Wide-format uploaded file
            fips_col  = next(c for c in df_map.columns if "fips" in c.lower())
            num_cols  = get_numeric_cols(df_map)
            indicator = st.selectbox("Column to map", num_cols, key="map_indicator")
            df_wide   = df_map[[fips_col, indicator]].dropna().copy()
            df_wide   = df_wide.groupby(fips_col, as_index=False)[indicator].mean()
            df_wide.rename(columns={fips_col: "fips", indicator: "value"}, inplace=True)
            df_wide["fips"] = df_wide["fips"].astype(str).str.zfill(5)

        c1, c2, c3 = st.columns(3)
        with c1: metric_card("Median", f"{df_wide['value'].median():.2f}")
        with c2: metric_card("Min",    f"{df_wide['value'].min():.2f}",  sub_class="good")
        with c3: metric_card("Max",    f"{df_wide['value'].max():.2f}",  sub_class="bad")

        hover = [c for c in ["county","state"] if c in df_wide.columns]
        rendered = _render_map(df_wide, "fips", "value",
                               hover_cols=hover, title=indicator)
        if not rendered:
            st.info("Could not render map — check that your FIPS codes are valid 5-digit strings.")

        section("📋 County Data Table")
        show_cols = [c for c in ["county","state","fips","value"] if c in df_wide.columns]
        styled_tbl = (
            df_wide[show_cols]
            .sort_values("value", ascending=False)
            .reset_index(drop=True)
        )
        st.dataframe(styled_tbl, use_container_width=True, height=300)

    # ── ZIP MAP ───────────────────────────────────────────────
    else:
        zip_col  = next(c for c in df_map.columns if c.lower() in
                        ["zip","zip_code","postal","zcta","zipcode"])
        num_cols = get_numeric_cols(df_map)
        if not num_cols:
            st.warning("No numeric columns found in your file.")
            return

        indicator = st.selectbox("Column to map", num_cols, key="map_zip_ind")
        df_z = df_map[[zip_col, indicator]].dropna().copy()
        df_z = df_z.groupby(zip_col, as_index=False)[indicator].mean()
        df_z["zip_str"] = df_z[zip_col].astype(str).str.zfill(5)
        df_z["fips5"]   = df_z["zip_str"]  # ZIP choropleth uses same GEOID field

        c1, c2, c3 = st.columns(3)
        with c1: metric_card("Median", f"{df_z[indicator].median():.2f}")
        with c2: metric_card("Min",    f"{df_z[indicator].min():.2f}",  sub_class="good")
        with c3: metric_card("Max",    f"{df_z[indicator].max():.2f}",  sub_class="bad")

        # Risk tier column for color
        df_z["Risk Tier"] = assign_risk_tier(df_z[indicator])

        # Bar chart — top ZIPs
        color_map = {t: RISK_COLORS[t] for t in ["Critical","High","Moderate","Low","Unknown"]}
        top_n = min(40, len(df_z))
        bar = (
            alt.Chart(df_z.head(top_n))
            .mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
            .encode(
                y=alt.Y("zip_str:N", sort="-x", title="ZIP Code",
                        axis=alt.Axis(labelLimit=70)),
                x=alt.X(f"{indicator}:Q", title=indicator),
                color=alt.Color("Risk Tier:N",
                    scale=alt.Scale(domain=list(color_map.keys()),
                                    range=list(color_map.values())),
                    legend=alt.Legend(title="Risk Tier")),
                tooltip=[
                    alt.Tooltip("zip_str:N",       title="ZIP"),
                    alt.Tooltip(f"{indicator}:Q",   title=indicator, format=".2f"),
                    alt.Tooltip("Risk Tier:N"),
                ],
            )
            .properties(
                height=max(300, top_n * 20),
                title=f"Top {top_n} ZIPs by {indicator}",
            )
        )
        st.altair_chart(bar, use_container_width=True)

        section("📋 ZIP Data Table")
        show = df_z[["zip_str", indicator, "Risk Tier"]].rename(
            columns={"zip_str": "ZIP Code"}
        ).sort_values(indicator, ascending=False).reset_index(drop=True)

        tier_bg = {"Critical":"#3d1a1a","High":"#3d2c0a",
                   "Moderate":"#1a2540","Low":"#0f2d1a"}
        tier_fg = {"Critical":"#ef4444","High":"#f59e0b",
                   "Moderate":"#60a5fa","Low":"#22c55e"}

        def _style(row):
            bg = tier_bg.get(row["Risk Tier"], "#161b27")
            fg = tier_fg.get(row["Risk Tier"], "#e2e8f0")
            return [f"background:{bg}", f"background:{bg};color:#e2e8f0",
                    f"background:{bg};color:{fg};font-weight:700"]

        st.dataframe(
            show.style.apply(_style, axis=1).hide(axis="index"),
            use_container_width=True, height=350,
        )
        st.download_button(
            "⬇ Download ZIP table (CSV)",
            data=safe_csv(show),
            file_name="vitalview_zip_map.csv",
            mime="text/csv",
        )

# ================================================================
# TAB — ZIP HEATMAP & RISK INDEX
# ================================================================
def tab_zip_heatmap():
    disclaimer_banner()
    section("ZIP Code Heatmap & Risk Index")
    st.markdown(
        f"<p style='color:{THEME['muted']};font-size:0.9rem;margin-bottom:1.5rem;'>"
        "Upload a file with a <b>ZIP code column</b> and health indicators. "
        "VitalView scores each ZIP, color-codes it by risk tier, and surfaces "
        "priority areas so organizations know exactly where to focus.</p>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="vv-uploader">', unsafe_allow_html=True)
    zip_file = st.file_uploader(
        "Drop your ZIP-level file here",
        type=["csv", "xlsx", "xls"],
        key="zip_uploader",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    if zip_file is None:
        col1, _ = st.columns([1, 4])
        with col1:
            sample = pd.DataFrame({
                "zip_code":         ["60601","60602","60603","60604","60605"],
                "state":            ["IL"] * 5,
                "obesity_pct":      [34.2, 28.1, 31.5, 38.9, 25.4],
                "uninsured_pct":    [18.3, 11.2, 15.7, 22.1,  9.8],
                "food_insec_pct":   [21.0, 14.5, 17.2, 26.3, 12.1],
                "mental_hlth_days": [ 5.2,  3.8,  4.6,  6.1,  3.1],
                "no_car_pct":       [24.0, 16.3, 19.8, 28.5, 13.2],
            })
            st.download_button(
                "⬇ ZIP Template (CSV)",
                data=sample.to_csv(index=False),
                file_name="vitalview_zip_template.csv",
                mime="text/csv",
            )
        empty_state(
            "🗺️", "No ZIP data loaded",
            "Upload a file above or download the template to get started.",
        )
        return

    try:
        df_zip = load_file(zip_file)
    except ValueError as e:
        st.error(f"⚠️ Could not read file: {e}")
        return
    except Exception:
        st.error("⚠️ Something went wrong reading that file. Make sure it's a valid CSV or Excel file and try again.")
        return

    if df_zip is None or df_zip.empty:
        st.warning("File appears to be empty.")
        return

    cols         = df_zip.columns.tolist()
    num_candidates = get_numeric_cols(df_zip)
    zip_candidates = [
        c for c in cols
        if any(k in c.lower() for k in ["zip", "postal", "zipcode", "zcta", "zip_code"])
    ] or cols

    section("Configure Columns")
    cfg1, cfg2 = st.columns([1, 2])
    with cfg1:
        zip_col = st.selectbox("ZIP / postal code column", zip_candidates, key="zh_zip_col")
    with cfg2:
        if not num_candidates:
            st.warning("No numeric columns found.")
            return
        selected_inds = st.multiselect(
            "Health indicators to include",
            num_candidates,
            default=num_candidates[:min(5, len(num_candidates))],
            key="zh_indicators",
        )

    if not selected_inds:
        st.info("Select at least one indicator above.")
        return

    with st.spinner("Building ZIP risk index…"):
        df_index = build_zip_index(df_zip, zip_col, selected_inds)

    section("Risk Summary")
    tier_counts = df_index["composite_risk"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("🔴 Critical",  str(tier_counts.get("Critical", 0)),  sub_class="bad")
    with c2: metric_card("🟠 High",      str(tier_counts.get("High",     0)),  sub_class="warn")
    with c3: metric_card("🟡 Moderate",  str(tier_counts.get("Moderate", 0)))
    with c4: metric_card("🟢 Low",       str(tier_counts.get("Low",      0)),  sub_class="good")

    section("Filter by Risk Tier")
    tier_filter = st.multiselect(
        "Show tiers",
        ["Critical", "High", "Moderate", "Low"],
        default=["Critical", "High"],
        key="zh_tier_filter",
    )
    df_view = (
        df_index[df_index["composite_risk"].isin(tier_filter)]
        if tier_filter else df_index
    )

    section("Color-Coded ZIP Risk Index")

    # Build a clean display dataframe — no raw HTML, no rendering bugs
    display_rows = []
    for _, row in df_view.iterrows():
        risk    = str(row.get("composite_risk", "Unknown"))
        emoji   = RISK_EMOJI.get(risk, "⚪")
        score   = row.get("composite_score", 0)
        zip_val = row[zip_col]
        ind_parts = []
        for ind in selected_inds:
            ind_val = row.get(ind, None)
            ind_risk = str(row.get(f"{ind}_risk", ""))
            ind_e   = RISK_EMOJI.get(ind_risk, "")
            short   = ind.replace("_pct","").replace("_"," ").title()[:14]
            val_str = f"{ind_val:.1f}" if isinstance(ind_val,(int,float)) and not pd.isna(ind_val) else "—"
            ind_parts.append(f"{ind_e} {short}: {val_str}")
        display_rows.append({
            "#":              int(row["rank"]),
            "ZIP":            str(zip_val),
            "Risk Tier":      f"{emoji} {risk}",
            "Score":          f"{score*100:.0f}%",
            "Indicators":     "  |  ".join(ind_parts),
        })

    df_display = pd.DataFrame(display_rows)

    # Color rows by risk tier using pandas Styler
    tier_bg = {
        "🔴 Critical": "#3d1a1a",
        "🟠 High":     "#3d2c0a",
        "🟡 Moderate": "#1a2540",
        "🟢 Low":      "#0f2d1a",
    }
    tier_fg = {
        "🔴 Critical": "#ef4444",
        "🟠 High":     "#f59e0b",
        "🟡 Moderate": "#60a5fa",
        "🟢 Low":      "#22c55e",
    }

    def _style_row(row):
        tier  = row["Risk Tier"]
        bg    = tier_bg.get(tier,  "#161b27")
        color = tier_fg.get(tier,  "#e2e8f0")
        return [
            f"background:{bg};color:{color};font-weight:700;border-left:3px solid {color}",
            f"background:{bg};color:{color};font-weight:700;font-family:monospace",
            f"background:{bg};color:{color};font-weight:700",
            f"background:{bg};color:#e2e8f0;font-family:monospace",
            f"background:{bg};color:#94a3b8;font-size:0.8em",
        ]

    styled = df_display.style.apply(_style_row, axis=1).hide(axis="index")
    st.dataframe(styled, use_container_width=True, height=min(600, 60 + len(df_display)*38))

    section("Composite Risk by ZIP")
    color_map = {t: RISK_COLORS[t] for t in ["Critical","High","Moderate","Low","Unknown"]}
    bar = (
        alt.Chart(df_view.head(40))
        .mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            y=alt.Y(f"{zip_col}:N", sort="-x", title="ZIP Code",
                    axis=alt.Axis(labelLimit=80)),
            x=alt.X("composite_score:Q", title="Composite Risk Score (0–1)",
                    scale=alt.Scale(domain=[0, 1])),
            color=alt.Color(
                "composite_risk:N",
                scale=alt.Scale(
                    domain=list(color_map.keys()),
                    range=list(color_map.values()),
                ),
                legend=alt.Legend(title="Risk Tier"),
            ),
            tooltip=[
                alt.Tooltip(f"{zip_col}:N", title="ZIP"),
                alt.Tooltip("composite_score:Q", title="Score", format=".3f"),
                alt.Tooltip("composite_risk:N",  title="Tier"),
            ] + [alt.Tooltip(f"{ind}:Q", title=ind, format=".2f") for ind in selected_inds],
        )
        .properties(height=max(280, len(df_view.head(40)) * 22))
    )
    st.altair_chart(bar, use_container_width=True)

    section("Per-Indicator Distribution")
    ind_sel = st.selectbox("Inspect indicator", selected_inds, key="zh_ind_inspect")
    dist_df = df_index[[zip_col, ind_sel, f"{ind_sel}_risk"]].dropna(subset=[ind_sel])
    dist_chart = (
        alt.Chart(dist_df)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(f"{ind_sel}:Q", bin=alt.Bin(maxbins=25), title=ind_sel),
            y=alt.Y("count():Q", title="ZIP Codes"),
            color=alt.Color(
                f"{ind_sel}_risk:N",
                scale=alt.Scale(
                    domain=list(color_map.keys()),
                    range=list(color_map.values()),
                ),
                legend=alt.Legend(title="Risk Tier"),
            ),
            tooltip=[
                alt.Tooltip(f"{ind_sel}:Q", bin=True),
                alt.Tooltip("count():Q", title="Count"),
            ],
        )
        .properties(height=240, title=f"Distribution of {ind_sel} across ZIPs")
    )
    st.altair_chart(dist_chart, use_container_width=True)

    section("Risk Tier Legend")
    legend_items = [
        ("🔴 Critical", "Critical", "Top 25% burden — highest-need ZIPs, prioritize now."),
        ("🟠 High",     "High",     "50–75th percentile — elevated need, strong candidates."),
        ("🟡 Moderate", "Moderate", "25–50th percentile — watch closely, may escalate."),
        ("🟢 Low",      "Low",      "Bottom 25% — relatively lower need, monitor and maintain."),
    ]
    leg_cols = st.columns(4)
    for i, (label, tier, desc) in enumerate(legend_items):
        color = RISK_COLORS[tier]
        with leg_cols[i]:
            st.markdown(f"""
            <div style="background:{THEME['card']};border:1px solid {color}44;
                        border-left:3px solid {color};border-radius:8px;padding:0.85rem;">
                <div style="font-weight:700;color:{color};font-size:0.88rem;
                            margin-bottom:0.3rem;">{label}</div>
                <div style="font-size:0.73rem;color:{THEME['muted']};line-height:1.4;">
                    {desc}</div>
            </div>""", unsafe_allow_html=True)

    section("Export ZIP Index")
    export_cols = (
        ["rank", zip_col, "composite_score", "composite_risk"]
        + selected_inds
        + [f"{ind}_risk" for ind in selected_inds]
    )
    export_cols = [c for c in export_cols if c in df_index.columns]
    df_export   = df_index[export_cols].copy()
    col1, col2  = st.columns(2)
    with col1:
        st.download_button(
            "⬇ Download Full Index (CSV)",
            data=safe_csv(df_export),
            file_name="vitalview_zip_risk_index.csv",
            mime="text/csv",
        )
    with col2:
        if HAS_XLSX:
            xb = to_excel(df_export)
            if xb:
                st.download_button(
                    "⬇ Download Full Index (Excel)",
                    data=xb,
                    file_name="vitalview_zip_risk_index.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

# ================================================================
# TAB — REPORTS / GRANT WRITER
# ================================================================
def tab_reports(df, features):
    disclaimer_banner()
    section("Grant Narrative Builder")
    if not dashboard_ready(df):
        empty_state(
            "📝", "No dashboard data loaded",
            "Upload a VitalView-schema file or enable Demo Mode to generate narratives.",
        )
        return

    dfx    = st.session_state.get("dfx", df)
    latest = int(dfx["year"].max()) if "year" in dfx.columns and not dfx.empty else None
    df_l   = dfx[dfx["year"] == latest].copy() if latest else dfx.copy()
    pivot  = derive_pivot(df_l)

    section("Indicator Weights")
    st.caption("Adjust how much each indicator contributes to the equity priority score.")
    weights = {}
    if pivot is not None and not pivot.empty:
        ind_cols = [c for c in pivot.columns if c not in ["state", "county", "fips"]]
        for i in range(0, len(ind_cols), 3):
            chunk  = ind_cols[i:i+3]
            wcols  = st.columns(len(chunk))
            for j, ind in enumerate(chunk):
                with wcols[j]:
                    weights[ind] = st.slider(
                        ind.split("(")[0].strip(),
                        0.0, 2.0, 1.0, 0.1,
                        key=f"rpt_w_{i+j}",
                    )

    priority_df = (
        compute_priority(pivot, weights)
        if pivot is not None and not pivot.empty
        else pd.DataFrame()
    )

    section("Narrative Configuration")
    c1, c2, c3 = st.columns(3)
    with c1: prog = st.text_input("Program Name", value="VitalView Community Health Initiative")
    with c2: pop  = st.text_input("Target Population", value="Low-income residents in priority counties")
    with c3: tf   = st.text_input("Timeframe", value="12 months")
    tone = st.selectbox("Tone", ["Equity-forward", "Neutral professional", "Impact-focused"])

    if st.button("🧠 Generate Narrative", type="primary", key="gen_narrative"):
        region = (
            ", ".join(sorted(dfx["state"].unique().tolist()))
            if "state" in dfx.columns else "selected region"
        )
        top3 = ""
        if not priority_df.empty:
            county_col = next(
                (c for c in ["county", "location"] if c in priority_df.columns), None
            )
            if county_col:
                top3 = ", ".join(
                    [str(r[county_col]) for _, r in priority_df.head(3).iterrows()]
                )
        tone_open = {
            "Equity-forward":
                "Grounded in equity principles, this proposal centers communities "
                "experiencing the greatest barriers to health.",
            "Neutral professional":
                "This proposal presents a data-driven plan to improve community health "
                "outcomes in identified priority areas.",
            "Impact-focused":
                "Designed to deliver measurable change, this initiative targets where "
                "interventions can have the greatest effect.",
        }[tone]
        trend_lines = []
        for ind in list(weights.keys())[:4]:
            sub = (
                dfx[dfx["indicator"] == ind]
                .groupby("year", as_index=False)["value"]
                .mean()
                .sort_values("year")
            )
            if len(sub) >= 2:
                try:
                    delta = float(sub["value"].iloc[-1]) - float(sub["value"].iloc[0])
                    d     = "increased" if delta > 0 else "decreased"
                    trend_lines.append(
                        f"- {ind} {d} by {abs(delta):.1f} over the analysis period."
                    )
                except Exception:
                    pass
        draft = (
            f"{prog} — Grant Draft\n"
            + "=" * 60 + "\n\n"
            + f"EXECUTIVE SUMMARY\n{tone_open} "
            + f"Using VitalView equity-weighted analysis for {region}, "
            + f"top-priority areas identified: {top3 or 'as shown in the data'}.\n\n"
            + f"STATEMENT OF NEED\n"
            + (f"In {latest}, " if latest else "")
            + "VitalView analysis highlights significant health disparities across the study region. "
            + f"Key indicators driving inequity: {', '.join(list(weights.keys())[:5]) or 'multiple determinants'}.\n\n"
            + "RECENT INDICATOR TRENDS\n"
            + ('\n'.join(trend_lines) if trend_lines else "- Trend data available in the Trends tab.")
            + f"\n\nTARGET POPULATION\n{pop}\n\n"
            + "PROPOSED STRATEGIES\n"
            + "- Data-informed outreach and enrollment navigation\n"
            + "- Food access supports (mobile markets, produce prescription programs)\n"
            + "- Behavioral health integration and peer support networks\n"
            + "- Transportation-aware program siting and voucher coordination\n"
            + "- Culturally-responsive lifestyle coaching and health education\n\n"
            + f"SMART OUTCOMES ({tf})\n"
            + "- Reduce top indicator disparity by ≥10% in priority ZIP codes\n"
            + "- Enroll 200+ residents in targeted wellness programs\n"
            + "- Increase SNAP/Medicaid enrollment by 15% in identified counties\n"
            + "- Establish 3+ active community partnerships with local nonprofits\n\n"
            + "EVALUATION PLAN\n"
            + "Quarterly equity-weighted tracking using VitalView dashboard. "
            + "Outcome indicators reported by county/ZIP with transparent community accountability.\n\n"
            + "Powered by VitalView — Community Health Intelligence Platform\n"
        )
        st.session_state["draft"] = draft
        audit(
            st.session_state["user"]["email"],
            "grant_draft",
            prog,
        )
        st.success("Draft generated.")

    draft = st.session_state.get("draft", "")
    if draft:
        section("Generated Draft")
        st.text_area("Copy or edit below:", value=draft, height=380, key="draft_display")
        section("Export Draft")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "⬇ TXT", data=draft.encode(),
                file_name="VitalView_Grant_Draft.txt", mime="text/plain",
            )
        with c2:
            if features.get("exports"):
                pdf = to_pdf(draft, title=f"{prog} — Grant Draft")
                if pdf:
                    st.download_button(
                        "⬇ PDF", data=pdf,
                        file_name="VitalView_Grant_Draft.pdf",
                        mime="application/pdf",
                    )
                else:
                    st.info("Install `reportlab` for PDF export.")
            else:
                st.info("PDF export is available on the Pro plan.")
        with c3:
            if st.button("💾 Save to History", key="save_hist"):
                if "narrative_history" not in st.session_state:
                    st.session_state["narrative_history"] = []
                st.session_state["narrative_history"].append({
                    "ts":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "text":  draft,
                    "label": prog,
                })
                st.success("Saved.")

    history = st.session_state.get("narrative_history", [])
    if history:
        section("Saved Narratives")
        for i, entry in enumerate(reversed(history)):
            with st.expander(f"{entry['ts']} — {entry['label']}"):
                st.text(entry["text"])
                st.download_button(
                    "⬇ Download", data=entry["text"].encode(),
                    file_name=f"narrative_{i}.txt", mime="text/plain",
                    key=f"hist_dl_{i}",
                )

# ================================================================
# AUTH PAGE
# ================================================================
def show_auth_page():
    T = THEME
    st.markdown(f"""
    <div style="text-align:center;padding:3rem 0 1.5rem;">
        <div style="display:flex;justify-content:center;margin-bottom:0.75rem;">
            {logo_img(96)}
        </div>
        <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;
                    color:{T['text']};letter-spacing:-0.03em;">
            Vital<span style="color:{T['accent']};">View</span>
        </div>
        <div style="color:{T['muted']};font-size:0.88rem;margin-top:0.35rem;">
            Community Health Intelligence Platform
        </div>
    </div>""", unsafe_allow_html=True)

    # ── Pre-login stats hero ──────────────────────────────────
    _border  = T["border"]
    _accent  = T["accent"]
    _muted   = T["muted"]
    stats = [
        ("3,200+", "Counties Tracked"),
        ("47",     "Health Indicators"),
        ("12M+",   "Residents Covered"),
        ("500+",   "Orgs Using VitalView"),
    ]
    cards_html = ""
    for v, l in stats:
        cards_html += (
            f'<div style="background:rgba(20,35,65,0.82);border:1px solid {_border};'
            f'border-radius:12px;padding:1.1rem;text-align:center;backdrop-filter:blur(8px);">'
            f'<div style="font-family:JetBrains Mono,monospace;font-size:1.55rem;'
            f'font-weight:500;color:{_accent};letter-spacing:-0.02em;">{v}</div>'
            f'<div style="font-size:0.71rem;color:{_muted};margin-top:0.3rem;'
            f'font-weight:600;text-transform:uppercase;letter-spacing:0.06em;">{l}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.85rem;'
        f'max-width:820px;margin:0 auto 1.5rem auto;">{cards_html}</div>'
        f'<div style="text-align:center;max-width:580px;margin:0 auto 1.5rem auto;'
        f'font-size:0.83rem;color:{_muted};line-height:1.6;">'
        f'VitalView gives public health teams, nonprofits, and grant writers '
        f'instant access to county-level health disparities — no data science degree required.'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Pilot notice banner ──────────────────────────────────
    st.markdown(f"""
    <div style="max-width:820px;margin:0 auto 1.25rem auto;
                background:#1a3a1a;border:1px solid #22c55e44;
                border-radius:10px;padding:0.75rem 1.1rem;
                display:flex;align-items:center;gap:0.75rem;">
        <span style="font-size:1.1rem;">🧪</span>
        <div style="font-size:0.78rem;color:#86efac;line-height:1.5;">
            <b>Closed Pilot Program</b> — You're accessing an early version of VitalView.
            Data and accounts may reset during updates. Do not store sensitive information.
            Questions? <a href="mailto:support@vitalview.health"
            style="color:#4ade80;">support@vitalview.health</a>
        </div>
    </div>""", unsafe_allow_html=True)

    mode = st.radio(
        "Auth mode", ["Log In", "Sign Up", "Reset Password"],
        horizontal=True, label_visibility="collapsed",
        key="auth_mode_radio",
    )

    if mode == "Log In":
        st.markdown('<div class="vv-auth-title">Welcome back</div>', unsafe_allow_html=True)
        email = st.text_input("Email",    key="li_email")
        pwd   = st.text_input("Password", type="password", key="li_pwd")
        if st.button("Log In", key="li_btn"):
            if not email or not pwd:
                st.error("Please enter your email and password.")
            else:
                user = verify_login(email, pwd)
                if user is None:
                    st.error("Incorrect email or password.")
                else:
                    st.session_state["user"] = dict(user)
                    st.rerun()

    elif mode == "Sign Up":
        st.markdown('<div class="vv-auth-title">Create your account</div>', unsafe_allow_html=True)
        name  = st.text_input("Full Name", key="su_name")
        email = st.text_input("Email",     key="su_email")
        pwd   = st.text_input("Password (8+ characters)", type="password", key="su_pwd")

        # ── Plan chooser with tier cards ─────────────────────
        st.markdown(f"""
        <div style='margin:1rem 0 0.5rem 0;font-size:0.75rem;font-weight:700;
                    color:{THEME["muted"]};text-transform:uppercase;letter-spacing:0.08em;'>
            Choose Your Plan
        </div>""", unsafe_allow_html=True)

        PLANS = {
            "free": {
                "label": "Free", "price": "$0/mo", "color": THEME["muted"],
                "features": ["✅ Dashboard & maps", "✅ Equity Scanner",
                              "✅ ZIP Heatmap", "❌ Exports (CSV only)",
                              "❌ AI Grant Writer", "❌ PDF reports"],
            },
            "pro": {
                "label": "Pro", "price": "$29/mo", "color": THEME["primary"],
                "features": ["✅ All Free features", "✅ Excel & PDF exports",
                              "✅ AI Grant Writer", "✅ Priority data filters",
                              "✅ Saved narratives", "❌ White-label"],
            },
            "educator": {
                "label": "Educator", "price": "$19/mo", "color": THEME["good"],
                "features": ["✅ All Pro features", "✅ Classroom data sets",
                              "✅ Student accounts", "✅ Tutorial mode",
                              "❌ White-label", "❌ API access"],
            },
            "enterprise": {
                "label": "Enterprise", "price": "Custom", "color": THEME["accent"],
                "features": ["✅ All Pro features", "✅ White-label branding",
                              "✅ API access", "✅ SSO / SAML",
                              "✅ Dedicated support", "✅ Custom integrations"],
            },
        }

        if "signup_plan" not in st.session_state:
            st.session_state["signup_plan"] = "free"

        cols = st.columns(4)
        for i, (pk, pv) in enumerate(PLANS.items()):
            with cols[i]:
                sel = st.session_state["signup_plan"] == pk
                border_c = pv["color"] if sel else THEME["border"]
                bg_c = f"{pv['color']}12" if sel else THEME["card"]
                feat_html = "".join(
                    f"<div style='font-size:0.68rem;color:{THEME['muted']};"
                    f"line-height:1.7;'>{f}</div>"
                    for f in pv["features"]
                )
                st.markdown(f"""
                <div style='border:2px solid {border_c};border-radius:10px;
                            padding:0.8rem 0.7rem;background:{bg_c};
                            cursor:pointer;transition:all .15s;'>
                    <div style='font-family:Syne,sans-serif;font-weight:800;
                                font-size:0.9rem;color:{pv["color"]};'>{pv["label"]}</div>
                    <div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;
                                font-weight:500;color:{THEME["text"]};
                                margin:0.2rem 0 0.5rem;'>{pv["price"]}</div>
                    {feat_html}
                </div>""", unsafe_allow_html=True)
                if st.button(f"Select {pv['label']}", key=f"plan_btn_{pk}",
                             use_container_width=True):
                    st.session_state["signup_plan"] = pk
                    st.rerun()

        plan = st.session_state["signup_plan"]
        selected_info = PLANS[plan]
        st.markdown(f"""
        <div style='margin:0.75rem 0;padding:0.6rem 0.9rem;
                    background:{selected_info["color"]}18;
                    border:1px solid {selected_info["color"]}44;
                    border-radius:8px;font-size:0.8rem;color:{THEME["text"]};'>
            Selected: <b style='color:{selected_info["color"]};'>{selected_info["label"]} — {selected_info["price"]}</b>
        </div>""", unsafe_allow_html=True)

        agreed = st.checkbox(
            "I understand this is a pilot — accounts may reset during updates, "
            "and I will not upload personally identifiable health information.",
            key="su_agree"
        )
        if st.button("Create Account", key="su_btn"):
            if not agreed:
                st.warning("⚠️ Please read and accept the pilot terms above.")
            else:
                ok, msg = add_user(name, email, pwd, plan)
                st.session_state["su_result"] = (ok, msg)
                st.rerun()

        if "su_result" in st.session_state:
            ok, msg = st.session_state.pop("su_result")
            if ok:
                st.success("Account created successfully. Please log in.")
            else:
                st.error(msg)

    else:
        st.markdown('<div class="vv-auth-title">Reset Password</div>', unsafe_allow_html=True)
        r_email = st.text_input("Your account email", key="rp_email")
        if st.button("Request Code", key="rp_req"):
            ok, result = start_reset(r_email)
            if ok:
                st.success(f"✅ {result}")
            else:
                st.error(f"❌ {result}")
        st.divider()
        r_code = st.text_input("Reset Code",   key="rp_code")
        r_new  = st.text_input("New Password", type="password", key="rp_new")
        if st.button("Set New Password", key="rp_set"):
            ok, msg = finish_reset(r_email, r_code, r_new)
            st.success(msg) if ok else st.error(msg)




# ================================================================
# TAB — AI GRANT WRITER (Pro / Enterprise only)
# ================================================================
def tab_ai_grant(df, features):
    T = THEME
    disclaimer_banner()

    # ── Gate: Pro/Enterprise only ─────────────────────────────
    if not features.get("ai_writer", False):
        st.markdown(f"""
        <div style="max-width:580px;margin:3rem auto;text-align:center;
                    background:{T["card"]};border:2px solid {T["primary"]}44;
                    border-radius:16px;padding:2.5rem;">
            <div style="font-size:2.5rem;margin-bottom:1rem;">🔒</div>
            <div style="font-family:Syne,sans-serif;font-size:1.2rem;
                        font-weight:800;color:{T["text"]};margin-bottom:0.75rem;">
                AI Grant Writer is a Pro Feature
            </div>
            <div style="color:{T["muted"]};font-size:0.85rem;line-height:1.7;
                        margin-bottom:1.5rem;">
                Upgrade to <b style="color:{T["primary"]};">Pro</b> or
                <b style="color:{T["accent"]};">Enterprise</b> to access the AI-powered
                grant narrative generator — drafts funder-ready proposals in seconds
                using your actual health data.
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;
                        margin-bottom:1.5rem;text-align:left;">
                {"".join([
                    f"<div style='font-size:0.8rem;color:{T['text']};'>"
                    f"✅ {f}</div>"
                    for f in [
                        "AI-drafted full proposal","HRSA, SAMHSA, CDC grant formats",
                        "Tone & funder customization","Section-by-section editing",
                        "Export to PDF & Word","Equity data auto-inserted",
                    ]
                ])}
            </div>
            <div style="background:{T["primary"]}18;border:1px solid {T["primary"]}44;
                        border-radius:8px;padding:0.75rem;font-size:0.8rem;
                        color:{T["primary"]};">
                ⚡ Contact <b>support@vitalview.health</b> to upgrade your plan
            </div>
        </div>""", unsafe_allow_html=True)
        return

    section("🤖 AI Grant Writer")

    # ── Data check ────────────────────────────────────────────
    if not dashboard_ready(df):
        empty_state("🤖","No data loaded",
                    "Load a dataset via the sidebar or enable Demo Mode to use AI Grant Writer.")
        return

    dfx = st.session_state.get("dfx", df)

    # ── Configuration ─────────────────────────────────────────
    st.markdown(f"""
    <div style="background:{T["card"]};border:1px solid {T["border"]};
                border-radius:12px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
        <div style="font-size:0.72rem;font-weight:700;color:{T["primary"]};
                    text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.75rem;">
            Grant Configuration
        </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        prog_name  = st.text_input("Program / Initiative Name",
                                   value="Community Health Equity Initiative",
                                   key="ai_prog")
        # ── Custom funder dropdown ────────────────────────────
        DEFAULT_FUNDERS = [
            "HRSA","SAMHSA","CDC","Robert Wood Johnson Foundation",
            "Kellogg Foundation","Health Foundation of South Florida",
            "United Way","Local Government","Other"
        ]
        if "custom_funders" not in st.session_state:
            st.session_state["custom_funders"] = []
        all_funders = DEFAULT_FUNDERS + st.session_state["custom_funders"]

        funder = st.selectbox("Target Funder Type", all_funders, key="ai_funder")

        # Add custom funder
        new_funder = st.text_input("➕ Add custom funder", placeholder="e.g. Peacock Foundation",
                                   key="ai_new_funder")
        if st.button("Add Funder", key="ai_add_funder"):
            nf = new_funder.strip()
            if nf and nf not in all_funders:
                st.session_state["custom_funders"].append(nf)
                st.success(f"✅ '{nf}' added to funder list!")
                st.rerun()
            elif nf in all_funders:
                st.warning("That funder is already in the list.")
        grant_type = st.selectbox("Grant Type",
                                  ["Community Health Needs Assessment","Substance Use",
                                   "Mental Health","Food Access","Housing & Health",
                                   "Maternal & Child Health","Chronic Disease Prevention"],
                                  key="ai_gtype")
    with c2:
        pop        = st.text_input("Target Population",
                                   value="Low-income residents in priority counties",
                                   key="ai_pop")
        budget     = st.selectbox("Budget Range",
                                  ["Under $50K","$50K–$150K","$150K–$500K",
                                   "$500K–$1M","Over $1M"],
                                  key="ai_budget")
        tone       = st.selectbox("Writing Tone",
                                  ["Equity-forward & urgent","Data-driven & professional",
                                   "Community-centered & warm","Impact-focused & concise"],
                                  key="ai_tone")

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Sections to generate ──────────────────────────────────
    section("Sections to Generate")
    all_sections = [
        "Executive Summary","Statement of Need","Target Population & Geography",
        "Program Description & Activities","Goals & SMART Objectives",
        "Evaluation Plan","Organizational Capacity","Budget Narrative",
        "Sustainability Plan","Letters of Support (template)",
    ]
    sel_sections = []
    cols = st.columns(2)
    for i, s in enumerate(all_sections):
        with cols[i % 2]:
            if st.checkbox(s, value=(i < 6), key=f"ai_sec_{i}"):
                sel_sections.append(s)

    # ── Build data context from loaded df ─────────────────────
    def build_data_context(dfx):
        lines = []
        if "state" in dfx.columns:
            states = dfx["state"].dropna().unique().tolist()
            lines.append(f"States: {', '.join(states)}")
        if "county" in dfx.columns:
            counties = dfx["county"].dropna().unique().tolist()
            lines.append(f"Counties ({len(counties)}): {', '.join(counties[:8])}"
                         + (" ..." if len(counties) > 8 else ""))
        if "indicator" in dfx.columns and "value" in dfx.columns:
            latest_yr = int(dfx["year"].max()) if "year" in dfx.columns else None
            df_l = dfx[dfx["year"]==latest_yr] if latest_yr else dfx
            for ind in df_l["indicator"].dropna().unique():
                vals = df_l[df_l["indicator"]==ind]["value"].dropna()
                if len(vals):
                    lines.append(f"  • {ind}: median={vals.median():.1f}, "
                                 f"min={vals.min():.1f}, max={vals.max():.1f}")
        return "\n".join(lines)

    data_ctx = build_data_context(dfx)

    # ── Generate button ───────────────────────────────────────
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    if not sel_sections:
        st.warning("Select at least one section to generate.")
        return

    generate = st.button("🤖 Generate with AI", type="primary",
                         key="ai_gen_btn", use_container_width=False)

    if "ai_draft_sections" not in st.session_state:
        st.session_state["ai_draft_sections"] = {}

    if generate:
        sections_list = "\n".join(f"- {s}" for s in sel_sections)
        prompt = f"""You are an expert public health grant writer. Write a compelling, 
funder-ready grant proposal for the following program using the real health data provided.

PROGRAM: {prog_name}
FUNDER TYPE: {funder}
GRANT TYPE: {grant_type}
TARGET POPULATION: {pop}
BUDGET RANGE: {budget}
WRITING TONE: {tone}

REAL HEALTH DATA FROM VITALVIEW ANALYSIS:
{data_ctx}

Write the following sections of the grant proposal. 
Use the actual health indicator data to support every claim. 
Be specific with numbers. Write each section under a clear header.
Sections to write:
{sections_list}

Format each section with:
## [SECTION NAME]
[Content — 2-4 substantial paragraphs per section]

Write in a {tone} voice. Be funder-ready, compelling, and data-grounded."""

        with st.spinner("✍️ AI is drafting your grant proposal..."):
            try:
                import requests as _req
                resp = _req.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 2000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    draft = resp.json()["content"][0]["text"]
                    st.session_state["ai_draft_sections"] = {
                        "draft": draft,
                        "prog":  prog_name,
                        "ts":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    st.success("✅ Draft generated!")
                    st.rerun()
                elif resp.status_code == 401:
                    st.error("API key missing or invalid. Set ANTHROPIC_API_KEY in your environment.")
                else:
                    st.error(f"API error {resp.status_code}: {resp.text[:300]}")
            except ImportError:
                st.error("Install `requests` to use AI generation: `pip install requests`")
            except Exception as e:
                st.error(f"Generation failed: {e}")

    # ── Show draft ────────────────────────────────────────────
    saved = st.session_state.get("ai_draft_sections", {})
    if saved.get("draft"):
        section(f"Generated Draft — {saved.get('prog','')} ({saved.get('ts','')})")

        # Render sections as expandable cards
        raw = saved["draft"]
        import re as _re
        parts = _re.split(r"(?m)^##\s+", raw)
        for part in parts:
            if not part.strip():
                continue
            lines   = part.strip().split("\n", 1)
            heading = lines[0].strip()
            body    = lines[1].strip() if len(lines) > 1 else ""
            with st.expander(f"📄 {heading}", expanded=True):
                st.markdown(body)

        # ── Exports ───────────────────────────────────────────
        section("Export")
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            st.download_button(
                "⬇ Download TXT",
                data=raw.encode(),
                file_name=f"VitalView_AI_Grant_{saved.get('prog','Draft')}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with ec2:
            if features.get("exports"):
                pdf = to_pdf(raw, title=f"{saved.get('prog','')} — AI Grant Draft")
                if pdf:
                    st.download_button(
                        "⬇ Download PDF",
                        data=pdf,
                        file_name=f"VitalView_AI_Grant_{saved.get('prog','Draft')}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
        with ec3:
            if st.button("🗑 Clear Draft", key="ai_clear", use_container_width=True):
                st.session_state["ai_draft_sections"] = {}
                st.rerun()

# ================================================================
# TAB — ADMIN PANEL (admin plan only)
# ================================================================
def tab_admin(user):
    T = THEME

    # Gate: admin plan only
    if user.get("plan") != "admin":
        st.markdown(f"""
        <div style="max-width:480px;margin:3rem auto;text-align:center;
                    background:{T["card"]};border:2px solid {T["danger"]}44;
                    border-radius:16px;padding:2.5rem;">
            <div style="font-size:2rem;margin-bottom:1rem;">🔒</div>
            <div style="font-size:1rem;font-weight:800;color:{T["text"]};">
                Admin access only
            </div>
        </div>""", unsafe_allow_html=True)
        return

    section("🛠 Admin Panel")

    # ── Load all users ────────────────────────────────────────
    try:
        if _use_supabase():
            sb = _sb()
            users_raw = sb.table("users").select(
                "id,name,email,plan,approved,created_at"
            ).order("created_at", desc=True).execute().data or []
            audit_raw = sb.table("audit_log").select(
                "email,action,detail,created_at"
            ).order("created_at", desc=True).limit(100).execute().data or []
        else:
            with get_conn() as conn:
                users_raw = [dict(r) for r in conn.execute(
                    "SELECT id, name, email, plan, approved, created_at FROM users ORDER BY created_at DESC"
                ).fetchall()]
                audit_raw = [dict(r) for r in conn.execute(
                    "SELECT email, action, detail, created_at FROM audit_log ORDER BY created_at DESC LIMIT 100"
                ).fetchall()]
    except Exception as e:
        st.error(f"Could not load admin data: {e}")
        return

    users_df = pd.DataFrame(users_raw) if users_raw else pd.DataFrame()
    audit_df = pd.DataFrame(audit_raw) if audit_raw else pd.DataFrame()

    # ── Summary metrics ───────────────────────────────────────
    if not users_df.empty:
        total     = len(users_df)
        by_plan   = users_df["plan"].value_counts().to_dict()
        pilot_cnt = by_plan.get("pro", 0) + by_plan.get("enterprise", 0)
        free_cnt  = by_plan.get("free", 0)

        c1, c2, c3, c4 = st.columns(4)
        with c1: metric_card("Total Users",    str(total))
        with c2: metric_card("Pilot / Pro",    str(pilot_cnt), "paid plans")
        with c3: metric_card("Free",           str(free_cnt))
        with c4: metric_card("Signups Today",  str(
            len(users_df[users_df["created_at"].str.startswith(
                datetime.now().strftime("%Y-%m-%d"), na=False)]) if "created_at" in users_df.columns else "—"
        ))

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── User management table ─────────────────────────────────
    section("User Management")

    if users_df.empty:
        st.info("No users yet.")
    else:
        # Search
        search = st.text_input("🔍 Search by name or email", key="admin_search")
        disp   = users_df.copy()
        if search:
            mask = (
                disp["name"].str.contains(search, case=False, na=False) |
                disp["email"].str.contains(search, case=False, na=False)
            )
            disp = disp[mask]

        for _, row in disp.iterrows():
            with st.expander(
                f"{'🟢' if row['approved'] else '🔴'}  {row['name']}  ·  {row['email']}  ·  {row['plan'].upper()}",
                expanded=False
            ):
                c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
                with c1:
                    st.caption(f"Joined: {str(row['created_at'])[:16]}")
                with c2:
                    new_plan = st.selectbox(
                        "Plan", ["free", "educator", "pro", "enterprise", "admin"],
                        index=["free","educator","pro","enterprise","admin"].index(
                            row["plan"] if row["plan"] in ["free","educator","pro","enterprise","admin"] else "free"
                        ),
                        key=f"plan_{row['id']}"
                    )
                with c3:
                    if st.button("💾 Save Plan", key=f"save_plan_{row['id']}",
                                 use_container_width=True):
                        try:
                            if _use_supabase():
                                _sb().table("users").update(
                                    {"plan": new_plan}
                                ).eq("id", row["id"]).execute()
                            else:
                                with get_conn() as conn:
                                    conn.execute(
                                        "UPDATE users SET plan=? WHERE id=?",
                                        (new_plan, row["id"])
                                    )
                                    conn.commit()
                            st.success(f"✅ Plan updated to {new_plan}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
                with c4:
                    if row["email"] not in ("admin@vitalview.com", "demo@vitalview.com"):
                        if st.button("🗑 Delete", key=f"del_{row['id']}",
                                     use_container_width=True):
                            try:
                                if _use_supabase():
                                    _sb().table("users").delete().eq("id", row["id"]).execute()
                                else:
                                    with get_conn() as conn:
                                        conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
                                        conn.commit()
                                st.success("User deleted.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")

    # ── Pilot management ──────────────────────────────────────
    section("Pilot Program Management")
    st.markdown(f"""
    <div style="background:{T["card"]};border:1px solid {T["border"]};
                border-radius:10px;padding:1rem 1.25rem;margin-bottom:1rem;">
        <div style="font-size:0.75rem;color:{T["muted"]};margin-bottom:0.75rem;">
            Create a pilot account for a new organization
        </div>""", unsafe_allow_html=True)

    pc1, pc2, pc3, pc4 = st.columns([2, 2, 1, 1])
    with pc1: p_name  = st.text_input("Org / Contact Name", key="pilot_name")
    with pc2: p_email = st.text_input("Email",              key="pilot_email")
    with pc3: p_plan  = st.selectbox("Plan", ["pro", "enterprise"], key="pilot_plan")
    with pc4:
        st.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        if st.button("➕ Create Pilot", key="create_pilot", use_container_width=True):
            if not p_name or not p_email:
                st.warning("Name and email required.")
            else:
                import secrets as _sec
                tmp_pwd = "Pilot" + _sec.token_hex(4).upper()
                ok, msg = add_user(p_name, p_email, tmp_pwd, p_plan)
                if ok:
                    st.success(
                        f"✅ Pilot account created!  "
                        f"Email: **{p_email}**  |  Temp password: **{tmp_pwd}**  "
                        f"(share securely — they can reset it)"
                    )
                else:
                    st.error(msg)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Audit log ─────────────────────────────────────────────
    section("Audit Log (last 100 events)")
    if audit_df.empty:
        st.info("No audit events yet.")
    else:
        st.dataframe(
            audit_df.rename(columns={
                "email": "User", "action": "Action",
                "detail": "Detail", "created_at": "Timestamp"
            }),
            use_container_width=True,
            hide_index=True,
        )

    # ── Export user list ──────────────────────────────────────
    section("Export")
    if not users_df.empty:
        csv = users_df.drop(columns=["id"], errors="ignore").to_csv(index=False)
        st.download_button(
            "⬇ Download User List (CSV)",
            data=csv,
            file_name=f"vitalview_users_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


# ================================================================
# TAB — GRANT FORM FILLER (Pro / Enterprise only)
# ================================================================
def tab_grant_form(df, features):
    T = THEME

    # ── Gate: Pro/Enterprise only ─────────────────────────────
    if not features.get("ai_writer", False):
        st.markdown(f"""
        <div style="max-width:580px;margin:3rem auto;text-align:center;
                    background:{T["card"]};border:2px solid {T["primary"]}44;
                    border-radius:16px;padding:2.5rem;">
            <div style="font-size:2.5rem;margin-bottom:1rem;">🔒</div>
            <div style="font-family:Syne,sans-serif;font-size:1.2rem;
                        font-weight:800;color:{T["text"]};margin-bottom:0.75rem;">
                Grant Form Filler is a Pro Feature
            </div>
            <div style="color:{T["muted"]};font-size:0.85rem;line-height:1.7;
                        margin-bottom:1.5rem;">
                Upgrade to <b style="color:{T["primary"]};">Pro</b> or
                <b style="color:{T["accent"]};">Enterprise</b> to upload any grant
                application and have VitalView auto-fill every section using your
                real health data.
            </div>
            <div style="background:{T["primary"]}18;border:1px solid {T["primary"]}44;
                        border-radius:8px;padding:0.75rem;font-size:0.8rem;
                        color:{T["primary"]};">
                ⚡ Contact <b>support@vitalview.health</b> to upgrade your plan
            </div>
        </div>""", unsafe_allow_html=True)
        return

    disclaimer_banner()
    section("📋 Grant Application Form Filler")

    st.markdown(f"""
    <div style="background:{T["card"]};border:1px solid {T["border"]};
                border-radius:12px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
        <div style="font-size:0.72rem;font-weight:700;color:{T["primary"]};
                    text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
            How it works
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.75rem;">
            {"".join([
                f"<div style='text-align:center;padding:0.75rem;background:{T['bg']};border-radius:8px;border:1px solid {T['border']};'>"
                f"<div style='font-size:1.4rem;margin-bottom:0.4rem;'>{icon}</div>"
                f"<div style='font-size:0.75rem;font-weight:700;color:{T['text']};'>{step}</div>"
                f"<div style='font-size:0.7rem;color:{T['muted']};margin-top:0.2rem;'>{desc}</div>"
                f"</div>"
                for icon, step, desc in [
                    ("📤", "1. Upload Form", "PDF or Word grant application"),
                    ("🔍", "2. VV Reads It", "Extracts all questions & limits"),
                    ("🤖", "3. AI Fills It", "Uses your health data to answer"),
                    ("⬇️", "4. Download", "Export completed Word document"),
                ]
            ])}
        </div>
    </div>""", unsafe_allow_html=True)

    # ── Step 1: Upload grant form ─────────────────────────────
    section("Step 1 — Upload Your Grant Application")
    st.caption("Supports PDF and Word (.docx) grant application forms. Your form is never stored — it stays in your session only.")

    grant_form_file = st.file_uploader(
        "Upload grant application form",
        type=["pdf", "docx"],
        key="grant_form_upload",
        help="Upload the grant application you want to fill out"
    )

    # ── Step 2: Configure ─────────────────────────────────────
    section("Step 2 — Configure Your Response")
    c1, c2 = st.columns(2)
    with c1:
        org_name    = st.text_input("Organization Name",
                                    value="Florida Health Justice Project",
                                    key="gf_org")
        prog_name   = st.text_input("Program / Initiative Name",
                                    value="Community Health Equity Initiative",
                                    key="gf_prog")
        target_pop  = st.text_input("Target Population",
                                    value="Low-income uninsured Floridians",
                                    key="gf_pop")
    with c2:
        budget      = st.selectbox("Budget Range",
                                   ["Under $50K","$50K–$150K","$150K–$500K",
                                    "$500K–$1M","Over $1M"],
                                   key="gf_budget")
        timeframe   = st.text_input("Project Timeframe",
                                    value="12 months",
                                    key="gf_timeframe")
        tone        = st.selectbox("Writing Tone",
                                   ["Equity-forward & urgent",
                                    "Data-driven & professional",
                                    "Community-centered & warm",
                                    "Impact-focused & concise"],
                                   key="gf_tone")

    # ── Step 3: Extract and generate ─────────────────────────
    if not dashboard_ready(df):
        st.warning("⚠️ Upload health data via the sidebar first so VitalView can ground your responses in real numbers.")

    if not grant_form_file:
        st.info("📂 Upload a grant application form above to continue.")
        return

    # Store original file bytes for PDF overlay export
    grant_form_file.seek(0)
    st.session_state["gf_orig_bytes"] = grant_form_file.read()
    grant_form_file.seek(0)

    # Extract text from uploaded form
    def extract_form_text(file):
        """Extract text from PDF or DOCX grant form."""
        import io
        suffix = file.name.lower()
        text = ""
        try:
            if suffix.endswith(".docx"):
                try:
                    import zipfile
                    import xml.etree.ElementTree as ET
                    file.seek(0)
                    with zipfile.ZipFile(io.BytesIO(file.read())) as z:
                        with z.open("word/document.xml") as doc:
                            tree = ET.parse(doc)
                            root = tree.getroot()
                            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                            paragraphs = root.findall(".//w:p", ns)
                            for para in paragraphs:
                                texts = para.findall(".//w:t", ns)
                                line  = "".join(t.text or "" for t in texts)
                                if line.strip():
                                    text += line.strip() + "\n"
                except Exception:
                    text = "Could not extract DOCX content."
            elif suffix.endswith(".pdf"):
                try:
                    from reportlab.lib.pagesizes import letter
                    file.seek(0)
                    raw = file.read()
                    # Basic PDF text extraction
                    import re as _re
                    chunks = _re.findall(rb'BT.*?ET', raw, _re.DOTALL)
                    for chunk in chunks[:50]:
                        strings = _re.findall(rb'\(([^)]{2,200})\)', chunk)
                        for s in strings:
                            try:
                                decoded = s.decode("latin-1").strip()
                                if len(decoded) > 3:
                                    text += decoded + "\n"
                            except Exception:
                                pass
                    if not text.strip():
                        text = "PDF text extraction limited. VitalView will generate comprehensive responses based on standard grant sections."
                except Exception:
                    text = "PDF text extraction limited. VitalView will generate comprehensive responses based on standard grant sections."
        except Exception as e:
            text = f"Could not read file: {e}"
        return text.strip()

    # Build data context
    def build_data_context(dfx):
        lines = []
        if dfx is None or dfx.empty:
            return "No health data uploaded."
        if "state" in dfx.columns:
            states = dfx["state"].dropna().unique().tolist()
            lines.append(f"States/Regions: {', '.join(states)}")
        if "county" in dfx.columns:
            counties = dfx["county"].dropna().unique().tolist()
            lines.append(f"Counties ({len(counties)}): {', '.join(counties[:10])}" +
                        (" ..." if len(counties) > 10 else ""))
        if "indicator" in dfx.columns and "value" in dfx.columns:
            latest_yr = int(dfx["year"].max()) if "year" in dfx.columns and not dfx["year"].isna().all() else None
            df_l = dfx[dfx["year"]==latest_yr] if latest_yr else dfx
            for ind in df_l["indicator"].dropna().unique():
                vals = df_l[df_l["indicator"]==ind]["value"].dropna()
                if len(vals):
                    lines.append(f"  • {ind}: mean={vals.mean():.1f}, min={vals.min():.1f}, max={vals.max():.1f}")
        return "\n".join(lines)

    # Standard grant sections to always address
    STANDARD_SECTIONS = [
        {
            "key": "needs_statement",
            "title": "Statement of Need / Problem Statement",
            "instruction": "Write a compelling, data-grounded needs statement. Use specific statistics from the health data. Explain why this community needs funding NOW. Reference disparities, gaps, and inequities. Be urgent and specific."
        },
        {
            "key": "target_population",
            "title": "Target Population & Geographic Area",
            "instruction": "Describe the target population in detail. Include demographics, geography, health status, barriers to care. Use the county and indicator data to be specific about who is being served and where."
        },
        {
            "key": "smart_objectives",
            "title": "Goals & SMART Objectives",
            "instruction": "Write 3-4 SMART objectives (Specific, Measurable, Achievable, Relevant, Time-bound). Each objective must include a baseline number from the data, a target improvement, and a timeframe. Format as numbered list."
        },
        {
            "key": "program_description",
            "title": "Program Description & Activities",
            "instruction": "Describe the program activities in detail. What will the organization DO with this funding? Include specific interventions, timelines, staff roles, and how activities connect to the stated need."
        },
        {
            "key": "evaluation_plan",
            "title": "Evaluation Plan",
            "instruction": "Write a rigorous evaluation plan. Include process metrics, outcome metrics tied to the SMART objectives, data collection methods, and how success will be measured. Reference the health indicators from the data."
        },
        {
            "key": "financial_forecast",
            "title": "Budget Narrative & Financial Forecast",
            "instruction": "Write a budget narrative that justifies the funding request. Break down how funds will be used (personnel, supplies, outreach, evaluation). Connect every budget line to a program activity. Be specific and defensible."
        },
        {
            "key": "org_capacity",
            "title": "Organizational Capacity",
            "instruction": "Describe the organization's capacity to implement this program. Include staff qualifications, past experience with similar grants, partnerships, infrastructure, and why this organization is uniquely positioned to do this work."
        },
        {
            "key": "sustainability",
            "title": "Sustainability Plan",
            "instruction": "Explain how the program will continue after the grant period ends. Include diverse funding strategies, earned revenue potential, partnerships, and long-term community impact."
        },
    ]

    # Generate button
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

    if "gf_results" not in st.session_state:
        st.session_state["gf_results"] = {}

    generate = st.button("📋 Extract & Fill Application", type="primary",
                         key="gf_generate", use_container_width=False)

    if generate:
        dfx = st.session_state.get("dfx", df)
        data_ctx  = build_data_context(dfx)
        form_text = extract_form_text(grant_form_file)

        prompt = f"""You are an expert grant writer filling out a grant application form on behalf of a community health organization.

ORGANIZATION: {org_name}
PROGRAM: {prog_name}
TARGET POPULATION: {target_pop}
BUDGET RANGE: {budget}
TIMEFRAME: {timeframe}
WRITING TONE: {tone}

REAL HEALTH DATA FROM VITALVIEW:
{data_ctx}

GRANT APPLICATION CONTENT DETECTED:
{form_text[:3000] if form_text else "Standard grant application format"}

INSTRUCTIONS:
You must fill out each of the following grant application sections. For every section:
1. Use the REAL health data statistics provided above — cite specific numbers
2. Stay within grant writing best practices for that section type
3. Write in a {tone} voice
4. Be specific, compelling, and funder-ready
5. Ground every claim in the data

Write each section clearly labeled. Be thorough but concise.

SECTIONS TO COMPLETE:
1. Statement of Need — compelling, data-driven, urgent
2. Target Population & Geographic Area — specific demographics and geography
3. Goals & SMART Objectives — 3-4 measurable objectives with baselines and targets
4. Program Description & Activities — what will be done, by whom, when
5. Evaluation Plan — how success will be measured, tied to objectives
6. Budget Narrative — justify the {budget} request line by line
7. Organizational Capacity — why this org can deliver
8. Sustainability Plan — how this continues after funding ends

Format each section with ## [SECTION NAME] followed by the content."""

        with st.spinner("📋 Reading your grant form and generating responses..."):
            try:
                import requests as _req
                try:
                    api_key = st.secrets.get("ANTHROPIC_API_KEY",
                                os.getenv("ANTHROPIC_API_KEY", ""))
                except Exception:
                    api_key = os.getenv("ANTHROPIC_API_KEY", "")

                resp = _req.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 3000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    raw = resp.json()["content"][0]["text"]
                    # Parse sections
                    import re as _re
                    parts = _re.split(r"(?m)^##\s+", raw)
                    results = {}
                    for part in parts:
                        if not part.strip():
                            continue
                        lines   = part.strip().split("\n", 1)
                        heading = lines[0].strip()
                        body    = lines[1].strip() if len(lines) > 1 else ""
                        results[heading] = body
                    st.session_state["gf_results"] = {
                        "sections": results,
                        "raw": raw,
                        "org": org_name,
                        "prog": prog_name,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "form_name": grant_form_file.name,
                    }
                    st.success("✅ Grant application filled successfully!")
                    st.rerun()
                elif resp.status_code == 401:
                    st.error("API key missing or invalid.")
                else:
                    st.error(f"Generation failed: {resp.status_code}")
            except ImportError:
                st.error("Install `requests`: pip install requests")
            except Exception as e:
                st.error(f"⚠️ Could not generate responses: {e}")

    # ── Step 4: Review & Edit ─────────────────────────────────
    saved = st.session_state.get("gf_results", {})
    if saved.get("sections"):
        section(f"Step 3 — Review & Edit — {saved.get('prog','')} ({saved.get('ts','')})")
        st.caption(f"Form: {saved.get('form_name','')} · Click any section to expand and edit")

        edited_sections = {}
        for heading, body in saved["sections"].items():
            with st.expander(f"📄 {heading}", expanded=False):
                edited = st.text_area(
                    "Edit response",
                    value=body,
                    height=250,
                    key=f"gf_edit_{heading[:30]}",
                    label_visibility="collapsed"
                )
                edited_sections[heading] = edited
                wc = len(edited.split())
                st.caption(f"Word count: {wc:,}")

        # Update session with edits
        if edited_sections:
            saved["sections"] = edited_sections

        # ── Step 5: Export ────────────────────────────────────
        section("Step 4 — Export Completed Application")

        def build_filled_pdf(saved_data, original_file_bytes=None):
            """
            Build a filled PDF:
            - If original PDF bytes provided: overlay answers onto original pages
            - Otherwise: build a clean standalone filled PDF
            """
            import io as _io
            from reportlab.lib.pagesizes import letter
            from reportlab.lib import colors
            from reportlab.lib.units import inch
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
            )
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

            buf = _io.BytesIO()

            # Try to overlay on original PDF first
            if original_file_bytes:
                try:
                    from pypdf import PdfReader, PdfWriter
                    from reportlab.pdfgen import canvas as rl_canvas

                    original_reader = PdfReader(_io.BytesIO(original_file_bytes))
                    writer = PdfWriter()

                    # Build overlay PDF with all answers
                    overlay_buf = _io.BytesIO()
                    c = rl_canvas.Canvas(overlay_buf, pagesize=letter)
                    page_w, page_h = letter

                    sections = saved_data.get("sections", {})
                    section_list = list(sections.items())

                    # Page 1 overlay — cover info
                    c.setFont("Helvetica-Bold", 9)
                    c.setFillColor(colors.HexColor("#003087"))
                    c.drawString(72, page_h - 72,
                        f"Organization: {saved_data.get('org','')}  |  "
                        f"Program: {saved_data.get('prog','')}  |  "
                        f"Generated: {saved_data.get('ts','')}")
                    c.showPage()

                    # One overlay page per original page
                    num_orig_pages = len(original_reader.pages)
                    sections_per_page = max(1, len(section_list) // max(num_orig_pages - 1, 1))

                    for page_idx in range(1, num_orig_pages):
                        start = (page_idx - 1) * sections_per_page
                        end   = start + sections_per_page
                        page_sections = section_list[start:end]

                        y = page_h - 100
                        for heading, body in page_sections:
                            if y < 80:
                                c.showPage()
                                y = page_h - 72
                            # Section heading
                            c.setFont("Helvetica-Bold", 8)
                            c.setFillColor(colors.HexColor("#003087"))
                            c.drawString(72, y, heading[:80])
                            y -= 14
                            # Body text — wrap lines
                            c.setFont("Helvetica", 8)
                            c.setFillColor(colors.black)
                            max_chars = 100
                            words = body.split()
                            line  = ""
                            for word in words:
                                if len(line + " " + word) <= max_chars:
                                    line = (line + " " + word).strip()
                                else:
                                    if y < 72:
                                        c.showPage()
                                        y = page_h - 72
                                    c.drawString(72, y, line)
                                    y -= 11
                                    line = word
                            if line:
                                if y < 72:
                                    c.showPage()
                                    y = page_h - 72
                                c.drawString(72, y, line)
                                y -= 11
                            y -= 8  # gap between sections
                        c.showPage()

                    c.save()
                    overlay_buf.seek(0)
                    overlay_reader = PdfReader(overlay_buf)

                    # Merge original pages with overlays
                    for i, orig_page in enumerate(original_reader.pages):
                        if i < len(overlay_reader.pages):
                            orig_page.merge_page(overlay_reader.pages[i])
                        writer.add_page(orig_page)

                    writer.write(buf)
                    buf.seek(0)
                    return buf.getvalue()

                except Exception:
                    pass  # Fall through to standalone PDF

            # Standalone filled PDF using ReportLab
            styles = getSampleStyleSheet()
            title_s = ParagraphStyle("gft", parent=styles["Title"],
                                     fontSize=14, textColor=colors.HexColor("#003087"),
                                     spaceAfter=6)
            h1_s = ParagraphStyle("gfh1", parent=styles["Heading1"],
                                  fontSize=11, textColor=colors.HexColor("#003087"),
                                  spaceBefore=14, spaceAfter=4)
            body_s = ParagraphStyle("gfb", parent=styles["Normal"],
                                    fontSize=10, leading=14, spaceAfter=8)
            meta_s = ParagraphStyle("gfm", parent=styles["Normal"],
                                    fontSize=9, textColor=colors.HexColor("#666666"),
                                    spaceAfter=4)

            doc = SimpleDocTemplate(buf, pagesize=letter,
                                    rightMargin=inch, leftMargin=inch,
                                    topMargin=inch, bottomMargin=inch)
            story = []

            story.append(Paragraph(
                f"{saved_data.get('org','')} — {saved_data.get('prog','')}",
                title_s
            ))
            story.append(Paragraph(
                f"Grant Application | Generated by VitalView | {saved_data.get('ts','')}",
                meta_s
            ))
            story.append(HRFlowable(width="100%", thickness=2,
                                    color=colors.HexColor("#003087")))
            story.append(Spacer(1, 0.2*inch))

            for heading, body in saved_data.get("sections", {}).items():
                story.append(Paragraph(heading, h1_s))
                story.append(HRFlowable(width="100%", thickness=0.5,
                                        color=colors.HexColor("#cccccc")))
                # Safely escape body text for ReportLab
                safe_body = (body
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
                for para in safe_body.split("\n\n"):
                    if para.strip():
                        story.append(Paragraph(para.strip(), body_s))
                story.append(Spacer(1, 0.1*inch))

            doc.build(story)
            buf.seek(0)
            return buf.getvalue()

        # Get original file bytes from session if available
        orig_bytes = st.session_state.get("gf_orig_bytes", None)

        ec1, ec2 = st.columns(2)

        with ec1:
            try:
                pdf_bytes = build_filled_pdf(saved, orig_bytes)
                fname = saved.get("prog", "Application").replace(" ", "_")
                st.download_button(
                    "⬇ Download Filled PDF",
                    data=pdf_bytes,
                    file_name=f"VitalView_Filled_{fname}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )
                st.caption("📄 Opens as a PDF — edit further in Adobe Acrobat or Preview")
            except Exception as e:
                st.error(f"PDF export failed: {e}")

        with ec2:
            if st.button("🗑 Clear & Start Over", key="gf_clear", use_container_width=True):
                st.session_state["gf_results"] = {}
                st.session_state["gf_orig_bytes"] = None
                st.rerun()

        # Security notice
        st.markdown(f"""
        <div style="margin-top:1rem;padding:0.75rem 1rem;background:{T["card"]};
                    border:1px solid {T["border"]};border-radius:8px;
                    font-size:0.75rem;color:{T["muted"]};">
            🔒 <b>Security:</b> Your grant application form was never stored on our servers.
            All processing happened in your browser session only.
            This session will be cleared when you close the tab.
        </div>""", unsafe_allow_html=True)


# ================================================================
# SIDEBAR
# ================================================================
def render_sidebar(user):
    T = THEME
    with st.sidebar:
        # ── Logo + identity ──────────────────────────────────────
        st.markdown(f"""
        <div style="padding:0.5rem 0 0.25rem 0;">
            <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.4rem;">
                {logo_img(32)}
                <span style="font-family:Syne,sans-serif;font-weight:800;
                             font-size:1.1rem;color:{T["text"]};">
                    Vital<span style="color:{T["accent"]};">View</span>
                </span>
            </div>
            <div style="font-size:0.78rem;color:{T["muted"]};padding-left:2px;">
                👋 {user["name"]} &nbsp;·&nbsp;
                <span style="color:{T["accent"]};font-weight:700;text-transform:uppercase;
                             font-size:0.68rem;">{user["plan"]}</span>
            </div>
        </div>""", unsafe_allow_html=True)

        if st.button("🚪 Log Out", key="logout_btn", use_container_width=True):
            audit(user["email"], "logout")
            st.session_state["user"] = None
            st.rerun()

        # ── Navigation guide ─────────────────────────────────────
        st.markdown(f"""
        <div style="margin:1rem 0 0.5rem 0;font-size:0.65rem;font-weight:700;
                    color:{T["primary"]};text-transform:uppercase;letter-spacing:0.1em;">
            Navigation
        </div>
        <div style="background:{T["card"]};border:1px solid {T["border"]};
                    border-radius:10px;padding:0.75rem;font-size:0.8rem;
                    color:{T["muted"]};line-height:2;">
            <div>📊 <b style="color:{T["text"]};">&nbsp;Dashboard</b> — overview &amp; quality</div>
            <div>⬆ <b style="color:{T["text"]};">&nbsp;Upload</b> — load your CSV/Excel</div>
            <div>⚖ <b style="color:{T["text"]};">&nbsp;Equity Scanner</b> — need vs service gaps</div>
            <div>🗺 <b style="color:{T["text"]};">&nbsp;Map</b> — county choropleth</div>
            <div>📍 <b style="color:{T["text"]};">&nbsp;ZIP Heatmap</b> — ZIP-level risk index</div>
            <div>📝 <b style="color:{T["text"]};">&nbsp;Reports</b> — grant narrative builder</div>
        </div>""", unsafe_allow_html=True)

        # ── Tier & features panel ─────────────────────────────
        plan_now = user.get("plan", "free")
        TIER_INFO = {
            "free":       {"color": T["muted"],   "label": "Free",
                           "features": ["✅ Dashboard & maps","✅ Equity Scanner",
                                        "✅ ZIP Heatmap","✅ CSV export",
                                        "❌ Excel/PDF exports","❌ AI Grant Writer"]},
            "educator":   {"color": T["good"],    "label": "Educator",
                           "features": ["✅ All Free features","✅ Tutorial mode",
                                        "✅ Classroom datasets","✅ CSV exports",
                                        "❌ AI Grant Writer","❌ PDF reports"]},
            "pro":        {"color": T["primary"], "label": "Pro",
                           "features": ["✅ All Free features","✅ Excel & PDF exports",
                                        "✅ AI Grant Writer","✅ Saved narratives",
                                        "✅ Priority filters","❌ White-label"]},
            "enterprise": {"color": T["accent"],  "label": "Enterprise",
                           "features": ["✅ All Pro features","✅ White-label branding",
                                        "✅ API access","✅ SSO/SAML login",
                                        "✅ Custom integrations","✅ Dedicated support"]},
        }
        tier = TIER_INFO.get(plan_now, TIER_INFO["free"])
        feat_html = "".join(
            f"<div style='line-height:1.8;font-size:0.72rem;'>{f}</div>"
            for f in tier["features"]
        )
        upgrade_html = (
            f"""<a href='#' style='display:block;margin-top:0.65rem;padding:0.45rem 0.6rem;
                background:{T["primary"]}22;border:1px solid {T["primary"]}55;
                border-radius:7px;font-size:0.72rem;color:{T["primary"]};
                text-decoration:none;font-weight:600;text-align:center;'>
                ⚡ Upgrade to Pro — unlock AI Grant Writer &amp; exports
            </a>"""
            if plan_now in ("free", "educator") else ""
        )
        st.markdown(f"""
        <div style="margin:0.75rem 0 0 0;font-size:0.65rem;font-weight:700;
                    color:{T["primary"]};text-transform:uppercase;letter-spacing:0.1em;">
            Your Plan
        </div>
        <div style="background:{T["card"]};border:2px solid {tier["color"]}55;
                    border-radius:10px;padding:0.75rem;">
            <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.5rem;">
                <span style="font-family:Syne,sans-serif;font-weight:800;
                             color:{tier["color"]};font-size:0.95rem;">{tier["label"]}</span>
                <span style="font-size:0.62rem;font-weight:700;padding:0.1rem 0.5rem;
                             border-radius:999px;background:{tier["color"]}22;
                             color:{tier["color"]};text-transform:uppercase;
                             letter-spacing:0.08em;">{plan_now}</span>
            </div>
            {feat_html}
            {upgrade_html}
        </div>""", unsafe_allow_html=True)

        # ── Upload with clear indicators ─────────────────────────
        st.markdown(f"""
        <div style="margin:1rem 0 0.5rem 0;font-size:0.65rem;font-weight:700;
                    color:{T["primary"]};text-transform:uppercase;letter-spacing:0.1em;">
            Data
        </div>""", unsafe_allow_html=True)

        demo = st.checkbox(
            "🧪 Use Demo Data (Illinois sample)",
            value=st.session_state.get("demo_mode", True),
            key="demo_toggle",
            help="Turn on to explore VitalView with built-in Illinois county data.",
        )
        st.session_state["demo_mode"] = demo

        # Upload guide callout
        st.markdown(f"""
        <div style="background:#0f2040;border:1px solid {T["primary"]}55;
                    border-left:3px solid {T["primary"]};border-radius:8px;
                    padding:0.65rem 0.75rem;margin:0.6rem 0;font-size:0.76rem;
                    color:{T["text"]};line-height:1.5;">
            <b>📋 Upload Format Guide</b><br>
            <span style="color:{T["muted"]};">
            <b style="color:{T["accent"]};">Dashboard/Map:</b>
            Needs columns: <code>state, county, fips, year, indicator, value, unit</code><br>
            <b style="color:{T["accent"]};">ZIP Heatmap:</b>
            Needs: <code>zip_code</code> + any numeric health columns<br>
            <b style="color:{T["accent"]};">Equity Scanner:</b>
            Needs: location + need metric + service metric
            </span>
        </div>""", unsafe_allow_html=True)

        # ── Multi-file upload: up to 3 files ────────────────────
        if "uploaded_files" not in st.session_state:
            st.session_state["uploaded_files"] = {}   # {name: df}
        if "active_file" not in st.session_state:
            st.session_state["active_file"] = None

        st.markdown(f"""
        <div style="font-size:0.65rem;font-weight:700;color:{T["primary"]};
                    text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.4rem;">
            Upload Files (up to 3)
        </div>""", unsafe_allow_html=True)

        sb_upload = st.file_uploader(
            "📂 Upload CSV or Excel",
            type=["csv", "xlsx", "xls"],
            key="sidebar_upload",
            help="Upload up to 3 files to compare. Each replaces the oldest if full.",
            accept_multiple_files=False,
        )
        if sb_upload:
            try:
                raw    = load_file(sb_upload)
                schema = enforce_schema(raw)
                df_to_store = schema if dashboard_ready(schema) else raw

                # Store in multi-file dict (max 3 — drop oldest if full)
                files = st.session_state["uploaded_files"]
                if sb_upload.name not in files:
                    if len(files) >= 3:
                        oldest = next(iter(files))
                        del files[oldest]
                files[sb_upload.name] = df_to_store
                st.session_state["uploaded_files"] = files
                st.session_state["active_file"]    = sb_upload.name

                # Set as primary df
                st.session_state["raw_df"]      = df_to_store
                st.session_state["upload_name"] = sb_upload.name
                if dashboard_ready(schema):
                    st.session_state["df"]        = schema
                    st.session_state["dfx"]       = schema
                    st.session_state["demo_mode"] = False
                    for _k in ("map_source", "map_indicator", "map_type"):
                        st.session_state.pop(_k, None)
                    st.success(f"✅ **{sb_upload.name}** — {len(schema):,} rows")
                else:
                    st.info(f"✅ Loaded {sb_upload.name} — use ZIP/Equity tabs")
            except Exception as e:
                st.error(f"⚠️ Could not load file. Check that it's a valid CSV or Excel file. ({type(e).__name__})")

        # ── File switcher: show loaded files as buttons ───────
        files = st.session_state.get("uploaded_files", {})
        if files:
            st.markdown(f"""
            <div style="font-size:0.65rem;font-weight:700;color:{T["primary"]};
                        text-transform:uppercase;letter-spacing:0.1em;
                        margin:0.75rem 0 0.35rem;">
                Loaded Files ({len(files)}/3)
            </div>""", unsafe_allow_html=True)
            for fname, fdf in files.items():
                is_active = st.session_state.get("active_file") == fname
                border    = T["accent"] if is_active else T["border"]
                rows      = len(fdf)
                label     = f"{'▶ ' if is_active else ''}{fname[:22]}{'…' if len(fname)>22 else ''} ({rows:,}r)"
                if st.button(label, key=f"switch_file_{fname}", use_container_width=True):
                    st.session_state["active_file"]    = fname
                    st.session_state["raw_df"]         = fdf
                    st.session_state["upload_name"]    = fname
                    if dashboard_ready(fdf):
                        st.session_state["df"]         = fdf
                        st.session_state["dfx"]        = fdf
                        st.session_state["demo_mode"]  = False
                        for _k in ("map_source", "map_indicator", "map_type"):
                            st.session_state.pop(_k, None)
                    st.rerun()

            # Compare mode toggle
            if len(files) >= 2:
                st.markdown(f"""
                <div style="font-size:0.65rem;font-weight:700;color:{T["primary"]};
                            text-transform:uppercase;letter-spacing:0.1em;
                            margin:0.75rem 0 0.35rem;">
                    Compare Mode
                </div>""", unsafe_allow_html=True)
                compare_on = st.toggle("Compare files side-by-side",
                                       key="compare_mode",
                                       value=st.session_state.get("compare_mode", False))
                if compare_on and len(files) >= 2:
                    fnames = list(files.keys())
                    st.session_state["compare_file_a"] = st.selectbox(
                        "File A", fnames, key="cmp_a")
                    st.session_state["compare_file_b"] = st.selectbox(
                        "File B", [f for f in fnames if f != st.session_state.get("compare_file_a")],
                        key="cmp_b")

            # Clear all button
            if st.button("🗑 Clear all files", key="clear_files", use_container_width=True):
                st.session_state["uploaded_files"] = {}
                st.session_state["active_file"]    = None
                st.session_state["upload_name"]    = None
                st.session_state["demo_mode"]      = True
                st.rerun()

        # ── Filters (only when schema data present) ──────────────
        df_cur = st.session_state.get("df", pd.DataFrame())
        if dashboard_ready(df_cur):
            st.markdown(f"""
            <div style="margin:1rem 0 0.5rem 0;font-size:0.65rem;font-weight:700;
                        color:{T["primary"]};text-transform:uppercase;letter-spacing:0.1em;">
                Filters
            </div>""", unsafe_allow_html=True)
            states       = sorted(df_cur["state"].dropna().unique().tolist())
            sel_states   = st.multiselect("State(s)", states, default=states, key="f_states")
            df_f         = df_cur[df_cur["state"].isin(sel_states)] if sel_states else df_cur
            counties     = sorted(df_f["county"].dropna().unique().tolist())
            sel_counties = st.multiselect("County(ies)", counties, key="f_counties")
            if sel_counties:
                df_f = df_f[df_f["county"].isin(sel_counties)]
            if "year" in df_f.columns:
                years = sorted(df_f["year"].dropna().unique().tolist())
                if len(years) > 1:
                    yr = st.slider(
                        "Year range",
                        int(min(years)), int(max(years)),
                        (int(min(years)), int(max(years))),
                        key="f_years",
                    )
                    df_f = df_f[
                        (df_f["year"] >= yr[0]) & (df_f["year"] <= yr[1])
                    ]
            st.session_state["dfx"] = df_f

        # ── Help footer ──────────────────────────────────────────
        st.markdown(f"""
        <div style="margin-top:1.5rem;padding-top:1rem;
                    border-top:1px solid {T["border"]};
                    font-size:0.72rem;color:{T["muted"]};line-height:1.6;">
            🔒 Your uploaded data is never stored.<br>
            All analysis runs in your session only.<br><br>
            <span style="color:{T["accent"]};">VitalView v2.1</span> · © 2025 Christopher Chaney
        </div>""", unsafe_allow_html=True)

# ================================================================
# MAIN
# ================================================================
def main():
    st.set_page_config(
        page_title="VitalView",
        page_icon="🏥",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    init_db()

    # Session state defaults
    for key, default in [
        ("user",      None),
        ("demo_mode", True),
        ("df",        pd.DataFrame()),
        ("dfx",       pd.DataFrame()),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # Auth gate
    if st.session_state["user"] is None:
        show_auth_page()
        return

    user     = st.session_state["user"]
    features = PLAN_FEATURES.get(user.get("plan", "free"), PLAN_FEATURES["free"])

    # Demo data
    if st.session_state["demo_mode"] and not dashboard_ready(st.session_state.get("df")):
        demo = get_demo_data()
        st.session_state["df"]  = demo
        st.session_state["dfx"] = demo

    render_sidebar(user)
    navbar(user.get("name", ""), user.get("plan", "free"))

    # ── Pilot stability banner (dismissable per session) ─────
    if not st.session_state.get("pilot_banner_dismissed"):
        T = THEME
        col_msg, col_btn = st.columns([10, 1])
        with col_msg:
            st.markdown(f"""
            <div style="background:#1c2a1c;border:1px solid #22c55e55;border-radius:8px;
                        padding:0.55rem 1rem;font-size:0.78rem;color:#86efac;margin-bottom:0.5rem;">
                🧪 <b>Pilot Mode</b> — This is an early-access version of VitalView.
                Accounts may reset when updates are deployed. Do not upload sensitive PII.
                Feedback? <a href="mailto:support@vitalview.health" style="color:#4ade80;">
                support@vitalview.health</a>
            </div>""", unsafe_allow_html=True)
        with col_btn:
            if st.button("✕", key="dismiss_pilot", help="Dismiss"):
                st.session_state["pilot_banner_dismissed"] = True
                st.rerun()

    df  = st.session_state.get("df",  pd.DataFrame())
    dfx = st.session_state.get("dfx", df)

    is_admin  = user.get("plan") == "admin"
    tab_labels = [
        "📊  Dashboard",
        "⬆  Upload",
        "⚖  Equity Scanner",
        "🗺  Map",
        "📍  ZIP Heatmap",
        "📝  Reports",
        "🤖  AI Grant Writer",
        "📋  Grant Form Filler",
    ]
    tab_fns = [
        lambda: tab_dashboard(dfx, features),
        tab_upload,
        tab_equity_scanner,
        lambda: tab_map(dfx),
        tab_zip_heatmap,
        lambda: tab_reports(dfx, features),
        lambda: tab_ai_grant(dfx, features),
        lambda: tab_grant_form(dfx, features),
    ]
    if is_admin:
        tab_labels.append("🛠  Admin")
        tab_fns.append(lambda: tab_admin(user))

    tabs = st.tabs(tab_labels)
    for i, (tab, fn) in enumerate(zip(tabs, tab_fns)):
        with tab:
            try:
                fn()
            except Exception as err:
                logger.exception("Tab %d error: %s", i, err)
                st.error(
                    f"Something went wrong in this tab. "
                    f"Please try again or contact support. ({type(err).__name__})"
                )

    st.markdown(
        f'''<div style="text-align:center;color:{THEME["muted"]};font-size:0.76rem;
                     border-top:1px solid {THEME["border"]};margin-top:3rem;padding-top:1rem;">
            © 2025 VitalView v2.1 &nbsp;·&nbsp; Christopher Chaney
            &nbsp;·&nbsp; Community health intelligence, built for action.
        </div>''',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
