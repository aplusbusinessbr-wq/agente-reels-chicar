"""
Dashboard — Chicar Mini Veículos · Reels -> YouTube Shorts
Execute com:  streamlit run dashboard.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os, json, pickle, datetime, subprocess, re
import requests
import streamlit as st
import pandas as pd
import altair as alt
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
POSTADOS_FILE  = os.path.join(BASE_DIR, "reels_postados.json")

PUBLER_API_KEY      = st.secrets.get("PUBLER_API_KEY", os.environ.get("PUBLER_API_KEY", ""))
PUBLER_WORKSPACE_ID = st.secrets.get("PUBLER_WORKSPACE_ID", os.environ.get("PUBLER_WORKSPACE_ID", ""))
PUBLER_TIKTOK_ID    = st.secrets.get("PUBLER_TIKTOK_ID", os.environ.get("PUBLER_TIKTOK_ID", ""))
PUBLER_BASE_URL     = "https://app.publer.com/api/v1"
FILA_FILE      = os.path.join(BASE_DIR, "fila_reels.json")
LOG_FILE       = os.path.join(BASE_DIR, "log_agente.txt")
LOG_AGENDADOR  = os.path.join(BASE_DIR, "log_agendador.txt")
RETRO_FILE     = os.path.join(BASE_DIR, "tiktok_retroativo.json")
TOKEN_FILE     = os.path.join(BASE_DIR, "token_youtube.pickle")
TASK_NAME      = "AgenteReelsChicar"
RUN_INTERVAL_H = 24  # tarefa diária; tolerância de "ainda online" é 26h

st.set_page_config(
    page_title="Chicar · Dashboard",
    page_icon="🏎️",
    layout="wide",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Big+Shoulders+Stencil+Display:wght@500;700;900&family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');

    :root {
        --bg-0: #0a0a0a;
        --bg-1: #131313;
        --bg-2: #1a1a1a;
        --line: #262626;
        --line-bright: #3a3a3a;
        --ink: #ededed;
        --ink-dim: #7a7a7a;
        --ink-faint: #5e5e5e;      /* era #4a4a4a — contraste 4.6:1 */
        --accent: #f7e600;
        --accent-warm: #ff5b00;
        --ok: #7dd87d;
        --warn: #f0a500;           /* era igual ao accent — agora âmbar */
        --bad: #ff4d6a;
        --text-micro: 0.6rem;      /* labels, tags, captions */
        --text-small: 0.72rem;     /* valores secundários, sub-labels */
    }

    /* Hide Streamlit chrome */
    [data-testid="stHeader"] { background: transparent; height: 0 !important; }
    #MainMenu, footer { visibility: hidden; }

    /* App surface — carbon black + faint blueprint grid + top accent line */
    .stApp {
        background:
            radial-gradient(ellipse 80% 50% at 50% -10%, rgba(247,230,0,0.05), transparent 60%),
            #0a0a0a;
        color: var(--ink);
    }
    .stApp::before {
        content: "";
        position: fixed; inset: 0;
        background-image:
            linear-gradient(rgba(255,255,255,0.018) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.018) 1px, transparent 1px);
        background-size: 48px 48px;
        pointer-events: none;
        z-index: 0;
    }
    .stApp::after {
        content: "";
        position: fixed; top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, transparent, var(--accent) 25%, var(--accent) 75%, transparent);
        z-index: 999;
        opacity: 0.9;
    }

    .stApp, .stApp p, .stApp span, .stApp div, .stApp label {
        font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
        color: var(--ink);
    }

    /* === Header strip === */
    .telemetry-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        padding: 4px 0 24px 0;
        border-bottom: 1px solid var(--line);
        margin-bottom: 36px;
    }
    .brand {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 900;
        font-size: 4.4rem;
        line-height: 0.85;
        letter-spacing: -0.02em;
        color: var(--ink);
    }
    .brand .accent { color: var(--accent); }
    .brand-sub {
        font-size: var(--text-small);
        text-transform: uppercase;
        letter-spacing: 0.34em;
        color: var(--ink-dim);
        margin-top: 10px;
    }
    .telemetry-meta { text-align: right; }
    .telemetry-meta .live {
        display: inline-flex; align-items: center; gap: 8px;
        font-size: var(--text-small); text-transform: uppercase;
        letter-spacing: 0.28em; color: var(--ok);
    }
    .telemetry-meta .live::before {
        content: "";
        width: 7px; height: 7px;
        background: var(--ok); border-radius: 50%;
        box-shadow: 0 0 10px var(--ok);
        animation: pulse 1.4s ease-in-out infinite;
    }
    .telemetry-meta .ts {
        margin-top: 8px; color: var(--ink);
        font-size: 0.92rem; letter-spacing: 0.08em;
    }
    @keyframes pulse {
        0%,100% { opacity: 1; transform: scale(1); }
        50%     { opacity: 0.3; transform: scale(0.85); }
    }

    /* === Status panel (online / last run / next run) === */
    .status-panel {
        display: grid;
        grid-template-columns: auto 1fr 1fr;
        gap: 36px;
        align-items: center;
        background: var(--bg-1);
        border: 1px solid var(--line);
        border-left: 3px solid var(--ok);
        padding: 20px 26px;
        margin-bottom: 36px;
        position: relative;
    }
    .status-panel.offline { border-left-color: var(--bad); }
    .status-panel.warn    { border-left-color: var(--warn); }
    .status-panel::after {
        content: "";
        position: absolute; right: 0; top: 0; bottom: 0;
        width: 1px;
        background: linear-gradient(180deg, transparent, var(--line) 40%, var(--line) 60%, transparent);
    }
    .status-state { display: flex; align-items: center; gap: 16px; }
    .status-dot {
        width: 11px; height: 11px; border-radius: 50%;
        background: var(--ok);
        box-shadow: 0 0 14px var(--ok), 0 0 0 4px rgba(125,216,125,0.08);
        animation: pulse 1.4s ease-in-out infinite;
        flex-shrink: 0;
    }
    .status-panel.offline .status-dot {
        background: var(--bad);
        box-shadow: 0 0 14px var(--bad), 0 0 0 4px rgba(255,77,106,0.08);
        animation: none;
    }
    .status-panel.warn .status-dot {
        background: var(--warn);
        box-shadow: 0 0 14px var(--warn), 0 0 0 4px rgba(247,230,0,0.10);
    }
    .status-label {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 900; font-size: 1.5rem; line-height: 1;
        text-transform: uppercase; letter-spacing: 0.04em;
        color: var(--ok);
    }
    .status-panel.offline .status-label { color: var(--bad); }
    .status-panel.warn    .status-label { color: var(--warn); }
    .status-sub {
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.26em; color: var(--ink-dim);
        margin-top: 6px;
    }
    .status-block { padding-left: 28px; border-left: 1px solid var(--line); }
    .status-block:first-of-type { padding-left: 0; border-left: none; }
    .status-key {
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.28em; color: var(--ink-dim);
        margin-bottom: 8px;
    }
    .status-val {
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.05rem; color: var(--ink);
        letter-spacing: 0.05em;
    }
    .status-rel {
        font-size: var(--text-small); color: var(--ink-faint);
        margin-top: 5px; letter-spacing: 0.18em;
        text-transform: uppercase;
    }

    /* === Stage / section markers === */
    .stage {
        display: flex; align-items: baseline; gap: 18px;
        margin: 44px 0 20px 0; padding-top: 20px;
        border-top: 1px solid var(--line);
    }
    .stage-num {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 900; font-size: 1.7rem;
        color: var(--accent); letter-spacing: 0.04em;
    }
    .stage-title {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 700; font-size: 1.7rem;
        text-transform: uppercase; letter-spacing: 0.04em;
        color: var(--ink);
    }
    .stage-tag {
        margin-left: auto;
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.28em; color: var(--ink-faint);
        border: 1px solid var(--line); padding: 5px 11px;
    }

    /* === KPI cluster === */
    .kpi {
        background: var(--bg-1);
        border: 1px solid var(--line);
        padding: 18px 18px 16px 18px;
        position: relative; overflow: hidden;
        opacity: 0; animation: rise 520ms cubic-bezier(.2,.8,.2,1) forwards;
        transition: border-color 160ms ease-out;
    }
    .kpi::before {
        content: ""; position: absolute; top: 0; left: 0;
        width: 28px; height: 1px; background: var(--accent);
    }
    .kpi:hover { border-color: var(--line-bright); background: #171717; }
    .kpi-label {
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.26em; color: var(--ink-dim);
        margin-bottom: 14px;
    }
    .kpi-value {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 700; font-size: 3.4rem; line-height: 1;
        letter-spacing: -0.02em; color: var(--ink);
    }
    .kpi-unit {
        display: inline-block; font-size: var(--text-micro);
        color: var(--ink-faint); margin-left: 6px;
        letter-spacing: 0.22em; text-transform: uppercase;
        vertical-align: super;
    }
    .kpi-bar {
        margin-top: 14px; height: 2px; background: var(--bg-2);
        position: relative; overflow: hidden;
    }
    .kpi-bar-fill {
        position: absolute; left: 0; top: 0; bottom: 0;
        width: var(--pct, 60%);
        background: linear-gradient(90deg, var(--accent), transparent);
    }

    /* === TikTok strip === */
    .tt-strip {
        display: flex; border: 1px solid var(--line);
        border-left: 3px solid var(--accent);
        background: var(--bg-1); margin-bottom: 36px;
    }
    .tt-strip-item {
        flex: 1; padding: 14px 22px;
        border-right: 1px solid var(--line);
    }
    .tt-strip-item:last-child { border-right: none; }
    .tt-strip-key {
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.28em; color: var(--ink-dim); margin-bottom: 6px;
    }
    .tt-strip-val {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 700; font-size: 1.55rem; color: var(--accent);
    }
    .tt-strip-sub {
        font-size: var(--text-micro); color: var(--ink-faint);
        letter-spacing: 0.14em; margin-top: 4px; text-transform: uppercase;
    }

    /* === Agenda timeline === */
    .agenda-day-label {
        text-transform: uppercase;
        letter-spacing: 0.3em; color: var(--accent);
        padding: 14px 0 10px 0; border-bottom: 1px solid var(--line-bright);
        margin-bottom: 2px; font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-size: 1rem; font-weight: 700;
    }
    .agenda-row {
        display: grid;
        grid-template-columns: 58px 88px 1fr 110px;
        gap: 16px; align-items: center;
        padding: 9px 0; border-bottom: 1px solid var(--line);
        font-size: 0.82rem;
    }
    .agenda-row.past { opacity: 0.35; }
    .agenda-time {
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-size: 1.15rem; font-weight: 700; color: var(--ink);
    }
    .agenda-plat-yt  { color: var(--ink); font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.12em; }
    .agenda-plat-tt  { color: var(--accent); font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.12em; }
    .agenda-titulo   { color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .agenda-st-pub    { color: var(--ok);      font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.1em; }
    .agenda-st-sch    { color: var(--warn);    font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.1em; }
    .agenda-st-onair  { color: var(--accent);  font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.1em; font-weight: 600; animation: pulse 1.4s ease-in-out infinite; }
    .agenda-st-other  { color: var(--ink-faint); font-size: var(--text-small); text-transform: uppercase; letter-spacing: 0.1em; }
    .agenda-row.now  { border-left: 2px solid var(--accent); padding-left: 8px; background: rgba(247,230,0,0.04); }
    @keyframes rise {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    [data-testid="column"]:nth-of-type(1) .kpi { animation-delay: 0ms; }
    [data-testid="column"]:nth-of-type(2) .kpi { animation-delay: 80ms; }
    [data-testid="column"]:nth-of-type(3) .kpi { animation-delay: 160ms; }
    [data-testid="column"]:nth-of-type(4) .kpi { animation-delay: 240ms; }
    [data-testid="column"]:nth-of-type(5) .kpi { animation-delay: 320ms; }

    /* === Streamlit widgets re-skin === */
    .stDataFrame { border: 1px solid var(--line) !important; }
    .stDataFrame [data-testid="stElementToolbar"] { display: none; }

    .stButton > button {
        background: transparent; color: var(--accent);
        border: 1px solid var(--accent); border-radius: 0;
        font-family: 'JetBrains Mono', monospace;
        font-weight: 600; font-size: 0.72rem;
        text-transform: uppercase; letter-spacing: 0.26em;
        padding: 14px 32px;
        transition: all 160ms cubic-bezier(.2,.8,.2,1);
    }
    .stButton > button:hover {
        background: var(--accent); color: var(--bg-0);
        transform: translate(-2px, -2px);
        box-shadow: 4px 4px 0 0 var(--ink);
    }
    .stButton > button:active {
        transform: translate(0, 0); box-shadow: 0 0 0 0 var(--ink);
    }

    [data-testid="stExpander"] {
        background: var(--bg-1); border: 1px solid var(--line); border-radius: 0;
    }
    [data-testid="stExpander"] summary {
        text-transform: uppercase; letter-spacing: 0.22em;
        font-size: 0.74rem; color: var(--ink-dim);
    }
    [data-testid="stExpander"] code, .stCode, pre {
        background: var(--bg-0) !important; color: var(--ink) !important;
        border: 1px solid var(--line) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.74rem !important;
    }

    [data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] {
        background: var(--bg-1); border: 1px solid var(--line); padding: 16px;
    }

    [data-testid="stAlert"] {
        background: var(--bg-1) !important;
        border: 1px solid var(--ok) !important; border-left: 3px solid var(--ok) !important;
        border-radius: 0 !important; color: var(--ok) !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.82rem !important;
    }

    .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stCaption"] {
        text-transform: uppercase; letter-spacing: 0.24em;
        color: var(--ink-dim) !important; font-size: 0.66rem !important;
    }

    /* Hide default streamlit hr; we have our own dividers */
    hr { display: none; }

    /* === Channel dividers === */
    .channel-divider {
        margin: 48px 0 28px 0; padding: 13px 22px;
        background: var(--bg-1); border: 1px solid var(--line);
        border-left: 3px solid var(--ink-faint);
        font-family: 'Big Shoulders Stencil Display', sans-serif;
        font-weight: 900; font-size: 1.1rem;
        text-transform: uppercase; letter-spacing: 0.12em; color: var(--ink);
    }
    .channel-divider.youtube { border-left-color: #fff; }
    .channel-divider.tiktok  { border-left-color: var(--accent); color: var(--accent); }
    .channel-divider.log     { border-left-color: var(--ink-faint); }

    /* Pit-strip footer */
    .pit-strip {
        margin-top: 56px; padding: 16px 0;
        border-top: 1px solid var(--line);
        display: flex; justify-content: space-between; align-items: center;
        font-size: var(--text-micro); text-transform: uppercase;
        letter-spacing: 0.28em; color: var(--ink-faint);
    }
    .pit-strip .dot {
        display: inline-block; width: 6px; height: 6px;
        background: var(--ok); border-radius: 50%; margin-right: 10px;
        box-shadow: 0 0 8px var(--ok);
    }

    .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stCaption"] {
        font-size: var(--text-micro) !important;
    }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
        .kpi { animation: none; opacity: 1; }
        .telemetry-meta .live::before,
        .status-dot,
        .agenda-st-onair { animation: none; }
    }
</style>
""", unsafe_allow_html=True)


# ── YouTube API ───────────────────────────────────────────────────────────────

@st.cache_resource(ttl=300)
def get_youtube():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


# ── Publer API ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def buscar_posts_publer_lista() -> list:
    """Retorna lista de todos os posts TikTok acessíveis no Publer (scheduled + published).
    Cada item: {dt_brt: datetime, text, post_link, state}. Ordenado por dt_brt."""
    try:
        hdrs = {
            "Authorization": f"Bearer-API {PUBLER_API_KEY}",
            "Publer-Workspace-Id": PUBLER_WORKSPACE_ID,
        }
        todos = []
        for state in ("scheduled", "published"):
            page = 1
            while True:
                r = requests.get(
                    f"{PUBLER_BASE_URL}/posts",
                    headers=hdrs,
                    params={"account_id": PUBLER_TIKTOK_ID, "state": state, "page": page},
                    timeout=15,
                )
                if r.status_code != 200:
                    break
                posts = r.json().get("posts", [])
                if not posts:
                    break
                for p in posts:
                    sched = p.get("scheduled_at", "")
                    if not sched:
                        continue
                    try:
                        dt = datetime.datetime.fromisoformat(sched[:19])
                    except Exception:
                        continue
                    todos.append({
                        "dt_brt":    dt,
                        "text":      p.get("text", ""),
                        "post_link": p.get("post_link") or "",
                        "state":     state,
                    })
                page += 1
                if len(posts) < 15:
                    break
        todos.sort(key=lambda x: x["dt_brt"])
        return todos
    except Exception:
        return []


def _enriquecer_retro(retro: list, publer_lista: list) -> tuple[list, int]:
    """Tenta preencher 'titulo' nas entradas sem título usando texto do Publer.
    Faz match por datetime exato (BRT). Retorna (retro_atualizado, n_preenchidos)."""
    from collections import defaultdict
    publer_by_slot = defaultdict(list)
    for p in publer_lista:
        key = p["dt_brt"].strftime("%Y-%m-%dT%H:%M")
        publer_by_slot[key].append(p)

    n = 0
    for entry in retro:
        if entry.get("titulo"):
            continue
        h = entry.get("horario", "")
        try:
            key = datetime.datetime.fromisoformat(h).strftime("%Y-%m-%dT%H:%M")
        except Exception:
            continue
        candidates = publer_by_slot.get(key, [])
        if candidates:
            match = candidates.pop(0)
            entry["titulo"] = match["text"]
            n += 1
    return retro, n


# ── Carrega dados locais ───────────────────────────────────────────────────────

def carregar_postados():
    if not os.path.exists(POSTADOS_FILE):
        return []
    with open(POSTADOS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    if dados and isinstance(dados[0], str):
        return [{"shortcode": s, "data": None, "youtube_id": None} for s in dados]
    return dados

def carregar_fila():
    if not os.path.exists(FILA_FILE):
        return []
    with open(FILA_FILE, encoding="utf-8") as f:
        return json.load(f)

def carregar_retro():
    if not os.path.exists(RETRO_FILE):
        return []
    with open(RETRO_FILE, encoding="utf-8") as f:
        return json.load(f)

def agenda_proximas_48h(postados, retro, stats):
    """Mescla posts YouTube de hoje e amanhã usando horário real da API do YouTube."""
    hoje   = datetime.date.today()
    amanha = hoje + datetime.timedelta(days=1)
    agora  = datetime.datetime.now()
    postados_map = {p["shortcode"]: p for p in postados}
    rows = []

    for p in postados:
        vid_id = p.get("youtube_id")
        if not vid_id:
            continue
        info = stats.get(vid_id, {})
        if not info:
            continue
        pub_str = info.get("publicacao", "")
        if not pub_str or pub_str == "—":
            continue
        try:
            # publicacao está em BRT: "dd/mm/yyyy HH:MM"
            dt_sort = datetime.datetime.strptime(pub_str, "%d/%m/%Y %H:%M")
        except Exception:
            continue
        d = dt_sort.date()
        if d not in (hoje, amanha):
            continue
        rows.append({
            "dt":         dt_sort,
            "data_label": "HOJE" if d == hoje else "AMANHÃ",
            "horario":    dt_sort.strftime("%H:%M"),
            "plataforma": "YouTube",
            "titulo":     info.get("titulo") or p.get("shortcode", "—"),
            "status":     info.get("status", "—"),
            "passado":    dt_sort < agora,
        })

    visto_tt = set()
    for r in retro:
        h_str = r.get("horario", "")
        if not h_str:
            continue
        try:
            dt = datetime.datetime.fromisoformat(h_str)
            d  = dt.date()
        except Exception:
            continue
        if d not in (hoje, amanha):
            continue
        dt_key = dt.isoformat()
        if dt_key in visto_tt:
            continue
        visto_tt.add(dt_key)
        shortcode = r["shortcode"]
        vid_id    = postados_map.get(shortcode, {}).get("youtube_id")
        titulo    = stats.get(vid_id, {}).get("titulo") or shortcode
        rows.append({
            "dt":          dt,
            "data_label":  "HOJE" if d == hoje else "AMANHÃ",
            "horario":     dt.strftime("%H:%M"),
            "plataforma":  "TikTok",
            "titulo":      titulo,
            "status":      "Publer ✓" if r.get("publer_job_id") else "—",
            "passado":     dt < agora,
        })

    rows.sort(key=lambda x: x["dt"])
    return rows

def tiktok_resumo(retro):
    agora    = datetime.datetime.now()
    total    = sum(1 for r in retro if r.get("publer_job_id"))
    futuros  = sorted(
        [r for r in retro if r.get("horario") and
         datetime.datetime.fromisoformat(r["horario"]) > agora],
        key=lambda x: x["horario"]
    )
    proximo  = futuros[0] if futuros else None
    hoje_str = datetime.date.today().isoformat()
    hoje_tt  = sum(1 for r in retro if r.get("horario", "").startswith(hoje_str))
    return total, futuros, proximo, hoje_tt

def ler_log(n=40):
    if not os.path.exists(LOG_FILE):
        return "Nenhum log encontrado."
    with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
        linhas = f.readlines()
    return "".join(linhas[-n:])


# ── Status do agente (online / última / próxima execução) ─────────────────────

def _humano_delta(td: datetime.timedelta, futuro: bool) -> str:
    s = int(td.total_seconds())
    if s < 0:
        s = -s
        futuro = not futuro
    if s < 60:        txt = f"{s}s"
    elif s < 3600:    txt = f"{s//60}min"
    elif s < 86400:   txt = f"{s//3600}h {(s%3600)//60:02d}min"
    else:             txt = f"{s//86400}d {(s%86400)//3600:02d}h"
    return f"em {txt}" if futuro else f"há {txt}"


STATUS_FILE = os.path.join(BASE_DIR, "status_agente.json")

@st.cache_data(ttl=60)
def status_agente():
    """Consulta o Windows Task Scheduler (se disponível) + status_agente.json + logs.
    {classe, label, sub, ultima_dt, proxima_dt, ultima_str, proxima_str,
     ultima_rel, proxima_rel}
    classe ∈ {"online", "warn", "offline"}.
    """
    import platform
    estado, last_result, last_dt, next_dt = None, None, None, None

    # 1) status_agente.json — escrito pelo agente após cada execução (funciona em qualquer OS)
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                st_data = json.load(f)
            if st_data.get("ultima_execucao"):
                last_dt = datetime.datetime.fromisoformat(st_data["ultima_execucao"])
            if st_data.get("proximo_agendado"):
                next_dt = datetime.datetime.fromisoformat(st_data["proximo_agendado"])
            last_result = 0 if st_data.get("status") == "ok" else 1
            estado = "Ready"
        except Exception:
            pass

    # 2) Windows Task Scheduler — apenas se rodando no Windows
    if platform.system() == "Windows":
        try:
            ps_cmd = (
                f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue;"
                "if ($t) { $i = $t | Get-ScheduledTaskInfo;"
                "Write-Output ('STATE=' + $t.State);"
                "Write-Output ('LAST=' + $i.LastRunTime.ToString('o'));"
                "Write-Output ('NEXT=' + $i.NextRunTime.ToString('o'));"
                "Write-Output ('RESULT=' + $i.LastTaskResult) }"
            )
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=8,
            )
            out = ps.stdout or ""
            if (m := re.search(r"STATE=(\S+)", out)):  estado = m.group(1)
            if (m := re.search(r"RESULT=(-?\d+)", out)): last_result = int(m.group(1))
            if (m := re.search(r"LAST=(\S+)", out)):
                try: last_dt = datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=None)
                except Exception: pass
            if (m := re.search(r"NEXT=(\S+)", out)):
                try: next_dt = datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=None)
                except Exception: pass
        except Exception:
            pass

    # 2) Parse ambos os logs para descobrir a execução mais recente
    PATTERN = r"\[(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\] (AGENTE INICIADO|FORCAR UPLOAD)"
    for log_path in (LOG_AGENDADOR, LOG_FILE):
        if not os.path.exists(log_path):
            continue
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                txt = f.read()
            matches = re.findall(PATTERN, txt)
            if matches:
                dt_log = datetime.datetime.strptime(matches[-1][0], "%d/%m/%Y %H:%M:%S")
                if last_dt is None or dt_log > last_dt:
                    last_dt = dt_log
        except Exception:
            pass

    # 3) Fallback p/ próxima execução: assume daily às 11:59
    if next_dt is None and last_dt is not None:
        candidato = last_dt.replace(hour=11, minute=59, second=0, microsecond=0) \
                    + datetime.timedelta(days=1)
        next_dt = candidato

    agora = datetime.datetime.now()

    # 4) Decide classe
    if estado is None and last_dt is None:
        classe, label, sub = "offline", "Sistema offline", "Sem registro de execução"
    elif estado == "Disabled":
        classe, label, sub = "offline", "Tarefa desabilitada", f"Task {TASK_NAME}"
    elif last_result not in (None, 0, 267011):  # 267011 = "task has not yet run"
        classe = "warn"
        label  = "Última execução falhou"
        sub    = f"Código {last_result} · {estado or '—'}"
    elif last_dt and (agora - last_dt).total_seconds() > (RUN_INTERVAL_H + 2) * 3600:
        classe, label, sub = "warn", "Execução atrasada", f"Sem rodar há mais de {RUN_INTERVAL_H+2}h"
    elif estado in ("Ready", "Running") or last_dt:
        classe = "online"
        label  = "Sistema online" if estado != "Running" else "Executando agora"
        sub    = f"Task Scheduler · {estado or 'ativo'}"
    else:
        classe, label, sub = "warn", "Estado indeterminado", "Verifique o agendador"

    fmt = lambda dt: dt.strftime("%d.%m.%Y · %H:%M") if dt else "—"
    return {
        "classe":      classe,
        "label":       label,
        "sub":         sub,
        "ultima_str":  fmt(last_dt),
        "proxima_str": fmt(next_dt),
        "ultima_rel":  _humano_delta(agora - last_dt, futuro=False) if last_dt else "—",
        "proxima_rel": _humano_delta(next_dt - agora, futuro=True)  if next_dt else "—",
    }


# ── Stats YouTube ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def buscar_stats(video_ids: tuple):
    yt = get_youtube()
    if not yt or not video_ids:
        return {}
    resultado = {}
    ids_validos = [v for v in video_ids if v]
    for i in range(0, len(ids_validos), 50):
        batch = ids_validos[i:i+50]
        resp = yt.videos().list(
            part="snippet,status,statistics",
            id=",".join(batch)
        ).execute()
        for item in resp.get("items", []):
            vid_id  = item["id"]
            snippet = item.get("snippet", {})
            status  = item.get("status", {})
            stats   = item.get("statistics", {})

            publish     = status.get("publishAt") or snippet.get("publishedAt", "")
            privacidade = status.get("privacyStatus", "")
            agora_utc   = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

            if privacidade == "private" and publish:
                pub_dt = datetime.datetime.strptime(publish[:19], "%Y-%m-%dT%H:%M:%S")
                status_label = "Agendado" if pub_dt > agora_utc else "Publicado"
            elif privacidade == "public":
                status_label = "Publicado"
            else:
                status_label = privacidade.capitalize()

            pub_brt = "—"
            if publish:
                try:
                    dt_utc = datetime.datetime.strptime(publish[:19], "%Y-%m-%dT%H:%M:%S")
                    dt_brt = dt_utc - datetime.timedelta(hours=3)
                    pub_brt = dt_brt.strftime("%d/%m/%Y %H:%M")
                except Exception:
                    pub_brt = publish[:16]

            resultado[vid_id] = {
                "titulo":      snippet.get("title", "—"),
                "status":      status_label,
                "publicacao":  pub_brt,
                "views":       int(stats.get("viewCount", 0)),
                "likes":       int(stats.get("likeCount", 0)),
                "comentarios": int(stats.get("commentCount", 0)),
            }
    return resultado


# ── Layout ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div class="telemetry-header">
  <div>
    <div class="brand">CHICAR<span class="accent">.</span></div>
    <div class="brand-sub">Mini Veículos · Reels Telemetry · Pit Lane Monitor</div>
  </div>
  <div class="telemetry-meta">
    <div class="live">Live Feed</div>
    <div class="ts">{datetime.datetime.now().strftime('%d.%m.%Y · %H:%M:%S')}</div>
  </div>
</div>
""", unsafe_allow_html=True)

postados = carregar_postados()
fila     = carregar_fila()
retro    = carregar_retro()
ids_yt   = tuple(p.get("youtube_id") for p in postados if p.get("youtube_id"))
stats    = buscar_stats(ids_yt)

# ── Painel de status do agente ────────────────────────────────────────────────
ag = status_agente()
st.markdown(f"""
<div class="status-panel {ag['classe']}">
  <div class="status-state">
    <span class="status-dot"></span>
    <div>
      <div class="status-label">{ag['label']}</div>
      <div class="status-sub">{ag['sub']}</div>
    </div>
  </div>
  <div class="status-block">
    <div class="status-key">Última execução</div>
    <div class="status-val">{ag['ultima_str']}</div>
    <div class="status-rel">{ag['ultima_rel']}</div>
  </div>
  <div class="status-block">
    <div class="status-key">Próxima execução</div>
    <div class="status-val">{ag['proxima_str']}</div>
    <div class="status-rel">{ag['proxima_rel']}</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Cálculos ──────────────────────────────────────────────────────────────────
total_views     = sum(v["views"]  for v in stats.values())
total_likes     = sum(v["likes"]  for v in stats.values())
agendados       = sum(1 for v in stats.values() if v["status"] == "Agendado")
publicados      = sum(1 for v in stats.values() if v["status"] == "Publicado")
n_postados      = len(postados)
tt_total, tt_futuros, tt_proximo, tt_hoje = tiktok_resumo(retro)
agenda          = agenda_proximas_48h(postados, retro, stats)
publer_lista    = buscar_posts_publer_lista()
postados_map_sc = {p["shortcode"]: p for p in postados}

def _pct(a, b): return f"{round(a / b * 100)}%" if b else "0%"

def _render_agenda(rows, plataforma):
    if not rows:
        st.info(f"Nenhum post {plataforma} agendado para hoje ou amanhã.")
        return
    agora    = datetime.datetime.now()
    dia_atual = None
    html = ""
    for row in rows:
        if row["data_label"] != dia_atual:
            dia_atual = row["data_label"]
            html += f'<div class="agenda-day-label">{dia_atual} · {row["dt"].strftime("%d.%m.%Y")}</div>'
        delta_min = abs((row["dt"] - agora).total_seconds()) / 60
        on_air    = delta_min <= 5
        past_cls  = "past" if row["passado"] and not on_air else ""
        now_cls   = "now" if on_air else ""
        plat_cls  = "agenda-plat-tt" if plataforma == "TikTok" else "agenda-plat-yt"
        plat_icn  = f"▶ {plataforma}"
        if on_air:
            st_cls, st_txt = "agenda-st-onair", "● ON AIR"
        elif row["status"] == "Publicado":
            st_cls, st_txt = "agenda-st-pub", row["status"]
        elif row["status"] in ("Agendado", "Publer ✓"):
            st_cls, st_txt = "agenda-st-sch", row["status"]
        else:
            st_cls, st_txt = "agenda-st-other", row["status"]
        titulo = (row["titulo"][:55] + "…") if len(row["titulo"]) > 55 else row["titulo"]
        html += f"""<div class="agenda-row {past_cls} {now_cls}">
  <span class="agenda-time">{row['horario']}</span>
  <span class="{plat_cls}">{plat_icn}</span>
  <span class="agenda-titulo">{titulo}</span>
  <span class="{st_cls}">{st_txt}</span>
</div>"""
    st.markdown(html, unsafe_allow_html=True)

# ── KPIs Sistema ──────────────────────────────────────────────────────────────
s1, s2, s3 = st.columns(3, gap="small")
for col, val, lbl, unit, pct in [
    (s1, n_postados, "Total processados", "REELS", "100%"),
    (s2, len(fila),  "Na fila YouTube",   "QUEUE", _pct(len(fila), max(n_postados, 1))),
    (s3, tt_total,   "TikTok agendados",  "POSTS", _pct(min(tt_total, n_postados), max(n_postados, 1))),
]:
    col.markdown(f"""
    <div class="kpi">
        <div class="kpi-label">{lbl}</div>
        <div class="kpi-value">{val}<span class="kpi-unit">{unit}</span></div>
        <div class="kpi-bar" style="--pct:{pct}"><div class="kpi-bar-fill"></div></div>
    </div>""", unsafe_allow_html=True)

# ─────────────────── YOUTUBE ──────────────────────────────────────────────────
st.markdown('<div class="channel-divider youtube">▶ YouTube · Shorts</div>', unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4, gap="small")
for col, val, lbl, unit, pct in [
    (c1, publicados,                              "Publicados",    "PUB", _pct(publicados, n_postados)),
    (c2, agendados,                               "Agendados",     "QUE", _pct(agendados, n_postados)),
    (c3, f"{total_views:,}".replace(",", "."),   "Visualizações", "VWS", _pct(min(total_views, 5000), 5000)),
    (c4, f"{total_likes:,}".replace(",", "."),   "Likes",         "LKS", _pct(min(total_likes, 500), 500)),
]:
    col.markdown(f"""
    <div class="kpi">
        <div class="kpi-label">{lbl}</div>
        <div class="kpi-value">{val}<span class="kpi-unit">{unit}</span></div>
        <div class="kpi-bar" style="--pct:{pct}"><div class="kpi-bar-fill"></div></div>
    </div>""", unsafe_allow_html=True)

# 01 // Agenda YouTube
st.markdown("""
<div class="stage">
  <span class="stage-num">01 //</span>
  <span class="stage-title">Agenda · YouTube</span>
  <span class="stage-tag">SCHEDULE · 48H</span>
</div>
""", unsafe_allow_html=True)
_render_agenda([r for r in agenda if r["plataforma"] == "YouTube"], "YouTube")

# 02 // Tabela de vídeos
st.markdown("""
<div class="stage">
  <span class="stage-num">02 //</span>
  <span class="stage-title">Vídeos enviados ao YouTube</span>
  <span class="stage-tag">OUT · Shorts</span>
</div>
""", unsafe_allow_html=True)

linhas = []
for p in sorted(postados, key=lambda x: x.get("data") or "", reverse=True):
    vid_id = p.get("youtube_id")
    info   = stats.get(vid_id, {})
    status = info.get("status", "Sem ID" if not vid_id else "—")
    linhas.append({
        "Data":        p.get("data", "—"),
        "Publicacao":  info.get("publicacao", "—"),
        "Titulo":      info.get("titulo", p.get("shortcode", "—")),
        "Status":      status,
        "Views":       info.get("views", None),
        "Likes":       info.get("likes", None),
        "Comentarios": info.get("comentarios", None),
        "Link":        f"https://youtube.com/shorts/{vid_id}" if vid_id else None,
    })

df = pd.DataFrame(linhas)
for col in ["Views", "Likes", "Comentarios"]:
    df[col] = pd.array(df[col], dtype=pd.Int64Dtype())

def colorir(val):
    if val == "Publicado": return "background-color:#0d1f0d; color:#7dd87d; font-weight:600"
    if val == "Agendado":  return "background-color:#1f1c0d; color:#f7e600; font-weight:600"
    return "color:#5e5e5e"

st.dataframe(
    df.style.map(colorir, subset=["Status"]),
    width="stretch",
    hide_index=True,
    column_config={
        "Link": st.column_config.LinkColumn("Link", display_text="Abrir"),
        "Views": st.column_config.NumberColumn("Views", format="%d"),
        "Likes": st.column_config.NumberColumn("Likes", format="%d"),
    }
)

# 03 // Fila pendente
st.markdown("""
<div class="stage">
  <span class="stage-num">03 //</span>
  <span class="stage-title">Fila pendente · YouTube</span>
  <span class="stage-tag">QUEUE · Backlog</span>
</div>
""", unsafe_allow_html=True)

if fila:
    df_fila = pd.DataFrame([{
        "Data agendada": item.get("data_agendada", "—"),
        "Shortcode":     item.get("shortcode", "—"),
        "Legenda":       (item.get("legenda") or "")[:80] + "…",
    } for item in fila])
    st.dataframe(df_fila, width="stretch", hide_index=True)
else:
    st.success("Fila vazia — todos os reels foram enviados ao YouTube.")

# 04 // Views por vídeo
dados_g = [{"Titulo": v["titulo"][:40], "Views": v["views"]} for v in stats.values() if v["views"] > 0]
if dados_g:
    st.markdown("""
    <div class="stage">
      <span class="stage-num">04 //</span>
      <span class="stage-title">Views por vídeo · YouTube</span>
      <span class="stage-tag">PERFORMANCE · Y-axis</span>
    </div>
    """, unsafe_allow_html=True)
    df_g = pd.DataFrame(dados_g).sort_values("Views", ascending=False)
    chart = (
        alt.Chart(df_g)
        .mark_bar(color="#f7e600", size=16)
        .encode(
            x=alt.X("Views:Q", axis=alt.Axis(
                labelColor="#7a7a7a", tickColor="#262626", domainColor="#262626",
                gridColor="#1a1a1a", titleColor="#7a7a7a", labelFont="JetBrains Mono",
            )),
            y=alt.Y("Titulo:N", sort="-x", axis=alt.Axis(
                labelColor="#ededed", labelFont="JetBrains Mono",
                tickColor="#262626", domainColor="#262626", titleColor="#7a7a7a",
            )),
            tooltip=["Titulo", "Views"],
        )
        .properties(height=max(180, len(df_g) * 28), background="#131313")
        .configure_view(strokeOpacity=0)
    )
    st.altair_chart(chart, use_container_width=True)

# ─────────────────── TIKTOK ───────────────────────────────────────────────────
st.markdown('<div class="channel-divider tiktok">▶ TikTok · via Publer</div>', unsafe_allow_html=True)

tt_proximo_str = datetime.datetime.fromisoformat(tt_proximo["horario"]).strftime("%d/%m · %H:%M") if tt_proximo else "—"
tt_proximo_sc  = tt_proximo["shortcode"] if tt_proximo else ""
st.markdown(f"""
<div class="tt-strip">
  <div class="tt-strip-item">
    <div class="tt-strip-key">Total agendado</div>
    <div class="tt-strip-val">{tt_total}</div>
    <div class="tt-strip-sub">posts via Publer</div>
  </div>
  <div class="tt-strip-item">
    <div class="tt-strip-key">Próximo post</div>
    <div class="tt-strip-val">{tt_proximo_str}</div>
    <div class="tt-strip-sub">{tt_proximo_sc}</div>
  </div>
  <div class="tt-strip-item">
    <div class="tt-strip-key">Hoje no TikTok</div>
    <div class="tt-strip-val">{tt_hoje}</div>
    <div class="tt-strip-sub">posts agendados</div>
  </div>
  <div class="tt-strip-item">
    <div class="tt-strip-key">Fila futura</div>
    <div class="tt-strip-val">{len(tt_futuros)}</div>
    <div class="tt-strip-sub">posts pendentes</div>
  </div>
</div>
""", unsafe_allow_html=True)

# 05 // Tabela de vídeos TikTok
st.markdown("""
<div class="stage">
  <span class="stage-num">05 //</span>
  <span class="stage-title">Vídeos enviados ao TikTok</span>
  <span class="stage-tag">OUT · via Publer</span>
</div>
""", unsafe_allow_html=True)

if retro:
    # Botão de enriquecimento: preenche 'titulo' ausentes com texto do Publer e salva JSON
    sem_titulo = sum(1 for r in retro if not r.get("titulo"))
    if sem_titulo > 0:
        col_enr, _ = st.columns([1, 4])
        with col_enr:
            if st.button(f"⟳  Buscar legendas no Publer ({sem_titulo} pendentes)"):
                retro_novo, n = _enriquecer_retro(list(retro), list(publer_lista))
                with open(RETRO_FILE, "w", encoding="utf-8") as _f:
                    json.dump(retro_novo, _f, indent=2, ensure_ascii=False)
                st.cache_data.clear()
                st.success(f"{n} legendas preenchidas. Recarregando…")
                st.rerun()

    # Monta lookup Publer por minuto exato para uso na tabela (multi-slot)
    from collections import defaultdict as _ddict
    _publer_by_min = _ddict(list)
    for _p in publer_lista:
        _publer_by_min[_p["dt_brt"].strftime("%Y-%m-%dT%H:%M")].append(_p)

    agora_tt = datetime.datetime.now()
    linhas_tt_full = []
    for r in sorted(retro, key=lambda x: x.get("horario", ""), reverse=True):
        sc     = r.get("shortcode", "—")
        vid_id = postados_map_sc.get(sc, {}).get("youtube_id")

        horario_raw = r.get("horario", "")
        try:
            h_dt        = datetime.datetime.fromisoformat(horario_raw)
            horario_fmt = h_dt.strftime("%d/%m/%Y %H:%M")
            slot_key    = h_dt.strftime("%Y-%m-%dT%H:%M")
        except Exception:
            h_dt        = None
            horario_fmt = horario_raw
            slot_key    = ""

        # Legenda: JSON salvo > Publer API (consome um por slot) > YouTube title > shortcode
        publ_candidates = _publer_by_min.get(slot_key, [])
        publ = publ_candidates.pop(0) if publ_candidates else {}
        legenda_raw = (r.get("titulo") or r.get("legenda") or
                       publ.get("text", "") or
                       stats.get(vid_id, {}).get("titulo") or sc)
        legenda_disp = (legenda_raw[:72] + "…") if len(legenda_raw) > 72 else legenda_raw

        taken_raw = r.get("taken_at", "")
        try:
            taken_fmt = datetime.datetime.fromisoformat(taken_raw).strftime("%d/%m/%Y")
        except Exception:
            taken_fmt = taken_raw[:10] if taken_raw else "—"

        if r.get("publer_job_id"):
            status_tt = "Publicado" if (h_dt and h_dt < agora_tt) else "Agendado"
        else:
            status_tt = "Pendente"

        # Link: TikTok URL (publicado) > YouTube Short > None
        tiktok_link = publ.get("post_link", "")
        if status_tt == "Publicado" and tiktok_link:
            link = tiktok_link
        elif vid_id:
            link = f"https://youtube.com/shorts/{vid_id}"
        else:
            link = None

        linhas_tt_full.append({
            "Instagram":  taken_fmt,
            "Agendado":   horario_fmt,
            "Legenda":    legenda_disp,
            "Status":     status_tt,
            "Views":      None,
            "Likes":      None,
            "Coments":    None,
            "Link":       link,
        })

    df_tt = pd.DataFrame(linhas_tt_full)
    for col in ["Views", "Likes", "Coments"]:
        df_tt[col] = pd.array(df_tt[col], dtype=pd.Int64Dtype())

    def colorir_tt(val):
        if val == "Publicado": return "background-color:#0d1f0d; color:#7dd87d; font-weight:600"
        if val == "Agendado":  return "background-color:#1f1c0d; color:#f7e600; font-weight:600"
        if val == "Pendente":  return "color:#5e5e5e"
        return "color:#5e5e5e"

    st.dataframe(
        df_tt.style.map(colorir_tt, subset=["Status"]),
        width="stretch",
        hide_index=True,
        column_config={
            "Link":    st.column_config.LinkColumn("Link", display_text="Abrir"),
            "Views":   st.column_config.NumberColumn("Views",   format="%d"),
            "Likes":   st.column_config.NumberColumn("Likes",   format="%d"),
            "Coments": st.column_config.NumberColumn("Coments", format="%d"),
        }
    )
    st.caption("Views/Likes/Coments: não disponíveis via Publer API · Publicado → link TikTok · Agendado → link YouTube Short")
else:
    st.info("Nenhum post retroativo TikTok encontrado.")

# ─────────────────── DIAGNÓSTICO ──────────────────────────────────────────────
st.markdown('<div class="channel-divider log">▶ Diagnóstico</div>', unsafe_allow_html=True)

# 08 // Log
st.markdown("""
<div class="stage">
  <span class="stage-num">06 //</span>
  <span class="stage-title">Log do agente</span>
  <span class="stage-tag">DIAG · últimas 40</span>
</div>
""", unsafe_allow_html=True)
with st.expander("Abrir saída do log →"):
    st.code(ler_log(40), language="text")

# ── Botão refresh + pit-strip ─────────────────────────────────────────────────
col_btn, _ = st.columns([1, 3])
with col_btn:
    if st.button("⟳  Atualizar telemetria"):
        st.cache_data.clear()
        st.rerun()

st.markdown("""
<div class="pit-strip">
  <div><span class="dot"></span>SISTEMA · OPERACIONAL</div>
  <div>CACHE · 5 MIN</div>
  <div>BUILD · CHICAR.PIT v1.0</div>
</div>
""", unsafe_allow_html=True)
