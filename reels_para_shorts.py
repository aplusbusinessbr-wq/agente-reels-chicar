"""
=============================================================
  AGENTE: Reels → YouTube Shorts
  Cliente: @chicarminiveiculos
  Dinâmica: roda a cada 2h (9h–21h) e posta no MESMO DIA o que apareceu — just-in-time
=============================================================
"""

import os
import re
import sys
import json
import time
import pickle
import datetime
import requests
import instaloader
import anthropic
import yt_dlp
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow  # mantido para compatibilidade
from google.auth.transport.requests import Request

sys.stdout.reconfigure(encoding="utf-8")

# Carrega .env se existir (credenciais locais — nunca commitadas)
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip()
_load_env()

IG_APP_ID = "936619743392459"

# Instagram Graph API oficial (token permanente de System User — sem cookies/login)
GRAPH_API_VER       = "v21.0"
ARQUIVO_TOKEN_IG    = "token_instagram.json"

# ─────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────
INSTAGRAM_PERFIL   = "chicarminiveiculos"
INSTAGRAM_LOGIN    = os.environ["INSTAGRAM_LOGIN"]
INSTAGRAM_SENHA    = os.environ["INSTAGRAM_SENHA"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
YOUTUBE_CHANNEL_ID = "UCG8BJRvVzQ0-FvEiBg6UQ2Q"
PASTA_DOWNLOAD     = "videos_baixados"
ARQUIVO_HISTORICO  = "reels_postados.json"
ARQUIVO_FILA       = "fila_reels.json"
HORA_INICIO        = 12   # legado (agendador antigo de slots fixos)
HORA_FIM           = 21   # legado
MAX_POR_DIA        = 3    # legado

# Dinâmica "just-in-time": espelha a conta — posta no mesmo dia o que apareceu,
# sem teto diário e sem rolar para os próximos dias. O agente roda a cada 2h.
JANELA_INICIO      = 9    # hora — não posta antes disso
JANELA_FIM         = 21   # hora — não posta a partir disso (vai pra manhã seguinte)
JIT_BUFFER_MIN     = 10   # margem após detectar o reel
JIT_ESPACO_MIN     = 15   # espaçamento entre reels da mesma rodada
FOTOS_MAX_DIA      = 6    # teto de posts de foto por dia no TikTok (backfill espalha)
FOTOS_HORAS        = [9, 11, 13, 15, 17, 19]  # slots fixos dos posts de foto
SESSAO_YOUTUBE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessao_youtube")


# ─────────────────────────────────────────
#  LOG
# ─────────────────────────────────────────

def log(msg):
    agora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    linha = f"[{agora}] {msg}"
    print(linha)
    with open("log_agente.txt", "a", encoding="utf-8") as f:
        f.write(linha + "\n")


# ─────────────────────────────────────────
#  HISTÓRICO E FILA
# ─────────────────────────────────────────

def carregar_historico():
    """Retorna lista de dicts {"shortcode": ..., "data": ...}.
    Migra automaticamente o formato antigo (lista de strings)."""
    if os.path.exists(ARQUIVO_HISTORICO):
        with open(ARQUIVO_HISTORICO, encoding="utf-8") as f:
            dados = json.load(f)
        if dados and isinstance(dados[0], str):
            # Migração: formato antigo sem data
            return [{"shortcode": s, "data": None} for s in dados]
        return dados
    return []

def shortcodes_postados(historico):
    """Retorna set de shortcodes já postados."""
    return {item["shortcode"] for item in historico}

def salvar_historico(historico):
    with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)

def carregar_fila():
    if os.path.exists(ARQUIVO_FILA):
        with open(ARQUIVO_FILA, encoding="utf-8") as f:
            return json.load(f)
    return []

def salvar_fila(fila):
    with open(ARQUIVO_FILA, "w", encoding="utf-8") as f:
        json.dump(fila, f, indent=2, ensure_ascii=False)

def proxima_data_disponivel(fila, historico, a_partir_de=None):
    """Legado — mantido para compatibilidade. Use proximo_slot_disponivel."""
    data = a_partir_de or datetime.date.today()
    while True:
        ds = data.isoformat()
        count_fila = sum(1 for item in fila    if item.get("data_agendada") == ds)
        count_hist = sum(1 for item in historico if item.get("data") == ds)
        if (count_fila + count_hist) < MAX_POR_DIA:
            return data
        data += datetime.timedelta(days=1)

def _clamp_janela(dt):
    """Mantém dt dentro da janela [JANELA_INICIO, JANELA_FIM).
    Antes da abertura → mesmo dia às JANELA_INICIO. A partir do fechamento → dia seguinte às JANELA_INICIO."""
    ini = datetime.time(JANELA_INICIO, 0)
    fim = datetime.time(JANELA_FIM, 0)
    if dt.time() < ini:
        return dt.replace(hour=JANELA_INICIO, minute=0, second=0, microsecond=0)
    if dt.time() >= fim:
        prox = dt + datetime.timedelta(days=1)
        return prox.replace(hour=JANELA_INICIO, minute=0, second=0, microsecond=0)
    return dt


def proximo_slot_jit(slot_anterior=None):
    """Agendador just-in-time: posta no mesmo dia, logo após a detecção, respeitando
    a janela de horário. Sem teto diário e sem rolar dias à frente — a cadência da
    conta se espelha naturalmente porque o agente roda várias vezes ao dia.
    1º reel da rodada: chame sem argumento. Demais: passe o slot anterior (espaça JIT_ESPACO_MIN)."""
    if slot_anterior is None:
        base = datetime.datetime.now() + datetime.timedelta(minutes=JIT_BUFFER_MIN)
    else:
        base = slot_anterior + datetime.timedelta(minutes=JIT_ESPACO_MIN)
    return _clamp_janela(base)


def proximo_slot_foto(historico):
    """Slot para posts de FOTO no TikTok: máx FOTOS_MAX_DIA/dia nos horários fixos
    FOTOS_HORAS. Ocupa o primeiro horário futuro livre do dia; cheio ou sem horário
    futuro → transborda para o dia seguinte. Backfill grande se espalha sozinho."""
    agora = datetime.datetime.now()
    ocupados = {(i["data"], i.get("horario_agendado", ""))
                for i in historico if i.get("tipo") == "foto" and i.get("data")}
    por_dia = {}
    for d, _h in ocupados:
        por_dia[d] = por_dia.get(d, 0) + 1
    data = datetime.date.today()
    while True:
        ds = data.isoformat()
        if por_dia.get(ds, 0) < FOTOS_MAX_DIA:
            for h in FOTOS_HORAS:
                dt = datetime.datetime.combine(data, datetime.time(h, 0))
                if dt > agora and (ds, dt.strftime("%H:%M")) not in ocupados:
                    return dt
        data += datetime.timedelta(days=1)


def proximo_slot_disponivel(historico):
    """Retorna o próximo datetime livre para agendamento (data + hora).
    Usa o historico para saber quais slots já estão ocupados, sem fila intermediária."""
    intervalo_min = (HORA_FIM - HORA_INICIO) * 60 // MAX_POR_DIA
    horas_fixas = [
        datetime.time((HORA_INICIO * 60 + i * intervalo_min) // 60,
                      (HORA_INICIO * 60 + i * intervalo_min) % 60)
        for i in range(MAX_POR_DIA)
    ]
    from collections import Counter
    count_por_dia = Counter(item["data"] for item in historico if item.get("data"))
    agora = datetime.datetime.now()
    data  = datetime.date.today()
    while True:
        ds = data.isoformat()
        n  = count_por_dia.get(ds, 0)
        if n < MAX_POR_DIA:
            dt = datetime.datetime.combine(data, horas_fixas[n])
            if dt > agora:
                return dt
        data += datetime.timedelta(days=1)


# ─────────────────────────────────────────
#  TIKTOK — PUBLER (agendador externo)
# ─────────────────────────────────────────

PUBLER_API_KEY      = os.environ["PUBLER_API_KEY"]
PUBLER_WORKSPACE_ID = os.environ["PUBLER_WORKSPACE_ID"]
PUBLER_TIKTOK_ID    = os.environ["PUBLER_TIKTOK_ID"]
PUBLER_BASE_URL     = "https://app.publer.com/api/v1"

def upload_via_publer(caminho_video: str, titulo: str,
                      horario: datetime.datetime = None) -> str:
    """Faz upload do vídeo e agenda no TikTok via Publer API."""
    headers_base = {
        "Authorization": f"Bearer-API {PUBLER_API_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE_ID,
    }

    # 1) Upload do arquivo de mídia (retry p/ blips de rede: ConnectionReset, timeout)
    log("  Publer: enviando vídeo...")
    resp = None
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            with open(caminho_video, "rb") as f:
                resp = requests.post(
                    f"{PUBLER_BASE_URL}/media",
                    headers=headers_base,
                    files={"file": (os.path.basename(caminho_video), f, "video/mp4")},
                    data={"direct_upload": "true"},
                    timeout=180,
                )
            break
        except requests.exceptions.RequestException as e:
            ultimo_erro = e
            if tentativa < 3:
                espera = 15 * tentativa
                log(f"  Publer: falha de rede no upload (tentativa {tentativa}/3): {e}. Aguardando {espera}s...")
                time.sleep(espera)
    if resp is None:
        raise Exception(f"Publer media upload falhou após 3 tentativas: {ultimo_erro}")
    if resp.status_code != 200:
        raise Exception(f"Publer media upload erro {resp.status_code}: {resp.text[:300]}")
    media_id = resp.json().get("id")
    if not media_id:
        raise Exception(f"Publer: media_id não retornado: {resp.json()}")
    log(f"  Publer: mídia enviada (id: {media_id})")

    # 2) Monta horário em UTC (BRT = UTC-3)
    agora = datetime.datetime.now()
    if horario and horario < agora:
        horario = horario + datetime.timedelta(days=1)
    if horario:
        scheduled_at = (horario + datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        scheduled_at = None

    # 3) Cria post agendado
    account_entry = {"id": PUBLER_TIKTOK_ID}
    if scheduled_at:
        account_entry["scheduled_at"] = scheduled_at

    body = {
        "bulk": {
            "state": "scheduled" if scheduled_at else "published",
            "posts": [{
                "networks": {
                    "tiktok": {
                        "type": "video",
                        "text": titulo[:2200],
                        "media": [{"id": media_id}],
                        "details": {
                            "privacy_level": "PUBLIC_TO_EVERYONE",
                            "disable_comment": False,
                            "disable_duet": False,
                            "disable_stitch": False,
                        },
                    }
                },
                "accounts": [account_entry],
            }],
        }
    }

    resp2 = None
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp2 = requests.post(
                f"{PUBLER_BASE_URL}/posts/schedule",
                headers={**headers_base, "Content-Type": "application/json"},
                json=body,
                timeout=60,
            )
            break
        except requests.exceptions.RequestException as e:
            ultimo_erro = e
            if tentativa < 3:
                espera = 15 * tentativa
                log(f"  Publer: falha de rede ao agendar (tentativa {tentativa}/3): {e}. Aguardando {espera}s...")
                time.sleep(espera)
    if resp2 is None:
        raise Exception(f"Publer schedule falhou após 3 tentativas: {ultimo_erro}")
    if resp2.status_code not in (200, 201):
        raise Exception(f"Publer schedule erro {resp2.status_code}: {resp2.text[:300]}")

    job_id = resp2.json().get("job_id", "desconhecido")
    if scheduled_at:
        log(f"  Publer agendado para {horario.strftime('%d/%m/%Y %H:%M')} | job_id: {job_id}")
    else:
        log(f"  Publer publicado | job_id: {job_id}")
    return job_id

# ─────────────────────────────────────────
#  TIKTOK — UPLOAD VIA CONTENT POSTING API (reserva — usar quando API for aprovada)
# ─────────────────────────────────────────

TIKTOK_TOKEN_FILE  = "token_tiktok.json"
TIKTOK_CLIENT_KEY    = os.environ["TIKTOK_CLIENT_KEY"]
TIKTOK_CLIENT_SECRET = os.environ["TIKTOK_CLIENT_SECRET"]

def get_tiktok_token():
    if not os.path.exists(TIKTOK_TOKEN_FILE):
        raise Exception("token_tiktok.json nao encontrado. Rode login_tiktok.py primeiro.")
    with open(TIKTOK_TOKEN_FILE, encoding="utf-8") as f:
        token = json.load(f)
    # Renova se expirado
    if time.time() > token.get("expires_at", 0) - 300:
        log("  TikTok: renovando token...")
        resp = requests.post("https://open.tiktokapis.com/v2/oauth/token/", data={
            "client_key":    TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": token["refresh_token"],
        })
        if resp.status_code != 200:
            raise Exception(f"Erro ao renovar token TikTok: {resp.text}")
        data = resp.json()
        token["access_token"]  = data["access_token"]
        token["refresh_token"] = data.get("refresh_token", token["refresh_token"])
        token["expires_at"]    = int(time.time()) + int(data.get("expires_in", 86400))
        with open(TIKTOK_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token, f, indent=2)
    return token

def upload_via_tiktok_api(caminho_video: str, titulo: str,
                           horario: datetime.datetime = None) -> str:
    """Faz upload para o TikTok via Content Posting API."""
    token     = get_tiktok_token()
    access_tk = token["access_token"]
    headers   = {"Authorization": f"Bearer {access_tk}", "Content-Type": "application/json"}

    file_size  = os.path.getsize(caminho_video)
    # Upload em chunk único (até 64 MB — suficiente para Reels)
    chunk_size = file_size
    n_chunks   = 1

    agora = datetime.datetime.now()
    if horario and horario < agora:
        horario = horario + datetime.timedelta(days=1)

    # Horário BRT -> Unix timestamp
    if horario:
        # Garante mínimo de 20 minutos no futuro (requisito TikTok)
        minimo = agora + datetime.timedelta(minutes=25)
        if horario < minimo:
            horario = minimo
        sched_ts  = int(horario.timestamp()) + 3 * 3600  # BRT->UTC não é necessário; timestamp já é local
        post_mode = "SCHEDULED_POST"
    else:
        sched_ts  = None
        post_mode = "DIRECT_POST"

    post_info = {
        "title":           titulo[:150],
        "privacy_level":   "SELF_ONLY" if token.get("sandbox") else "PUBLIC_TO_EVERYONE",
        "disable_duet":    False,
        "disable_comment": False,
        "disable_stitch":  False,
        "post_mode":       post_mode,
    }
    if sched_ts:
        post_info["scheduled_publish_time"] = sched_ts

    body = {
        "post_info": post_info,
        "source_info": {
            "source":            "FILE_UPLOAD",
            "video_size":        file_size,
            "chunk_size":        chunk_size,
            "total_chunk_count": n_chunks,
        },
    }

    # 1) Init upload
    resp = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers=headers, json=body
    )
    if resp.status_code != 200:
        raise Exception(f"TikTok init erro {resp.status_code}: {resp.text}")
    resp_data  = resp.json().get("data", {})
    upload_url = resp_data.get("upload_url")
    publish_id = resp_data.get("publish_id")
    if not upload_url:
        raise Exception(f"TikTok: upload_url nao retornado: {resp.json()}")

    # 2) Envia o arquivo em chunks
    with open(caminho_video, "rb") as f:
        for i in range(n_chunks):
            chunk = f.read(chunk_size)
            start = i * chunk_size
            end   = start + len(chunk) - 1
            upload_headers = {
                "Content-Type":  "video/mp4",
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(len(chunk)),
            }
            r = requests.put(upload_url, headers=upload_headers, data=chunk)
            if r.status_code not in (200, 201, 206):
                raise Exception(f"TikTok chunk {i} erro {r.status_code}: {r.text}")
            log(f"  TikTok upload: chunk {i+1}/{n_chunks}")

    if horario:
        log(f"  TikTok agendado para {horario.strftime('%d/%m/%Y %H:%M')} | publish_id: {publish_id}")
    else:
        log(f"  TikTok publicado | publish_id: {publish_id}")
    return publish_id or "desconhecido"


# ─────────────────────────────────────────
#  YOUTUBE — UPLOAD VIA DATA API v3
# ─────────────────────────────────────────

TOKEN_FILE      = "token_youtube.pickle"
CLIENT_SECRETS  = "client_secrets.json"
SCOPES          = ["https://www.googleapis.com/auth/youtube"]


def _oauth_youtube_via_playwright():
    """Renova o token OAuth do YouTube automaticamente usando a sessão
    salva em sessao_youtube/ (Chromium headless, aplusbusinessbr@gmail.com).
    Rode login_youtube.py uma vez para salvar a sessão."""
    import threading
    import http.server
    import urllib.parse
    from google_auth_oauthlib.flow import Flow
    from playwright.sync_api import sync_playwright

    # Porta aleatória livre
    _tmp = http.server.HTTPServer(("localhost", 0), http.server.BaseHTTPRequestHandler)
    port = _tmp.server_address[1]
    _tmp.server_close()

    redirect_uri = f"http://localhost:{port}"
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS, scopes=SCOPES,
                                         redirect_uri=redirect_uri)
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent",
                                         include_granted_scopes="true")

    callback = {"code": None, "done": threading.Event()}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            callback["code"] = params.get("code", [None])[0]
            callback["done"].set()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    TARGET_EMAIL = "bruna.chicar@gmail.com"  # proprietária do canal Chicar

    if not os.path.exists(SESSAO_YOUTUBE_DIR):
        raise Exception(
            "YouTube OAuth: sessao_youtube/ não encontrada. "
            "Rode login_youtube.py para salvar a sessão."
        )

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=SESSAO_YOUTUBE_DIR,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            log("  YouTube OAuth: usando sessao_youtube/ (Chromium)...")
            page = ctx.new_page()
            page.goto(auth_url, wait_until="networkidle", timeout=45000)

            # Passo 1: seletor de conta ("Escolha uma conta")
            for _ in range(3):
                content = page.content()
                url = page.url
                if "localhost" in url:
                    break  # já redirecionou para callback

                # Seletor de conta
                if "accounts.google.com/o/oauth2/v2/auth" in url or "Escolha uma conta" in content or "Choose an account" in content:
                    clicked = False
                    for sel in [
                        f'[data-email="{TARGET_EMAIL}"]',
                        f'li:has-text("{TARGET_EMAIL}")',
                        f'div[aria-label*="{TARGET_EMAIL}"]',
                        f'//li[.//text()[contains(.,"{TARGET_EMAIL}")]]',
                        f'//div[.//text()[contains(.,"{TARGET_EMAIL}")]][@role="link" or @role="button"]',
                    ]:
                        try:
                            if sel.startswith("//"):
                                loc = page.locator(f"xpath={sel}")
                            else:
                                loc = page.locator(sel)
                            if loc.count() > 0:
                                loc.first.click()
                                page.wait_for_load_state("networkidle", timeout=15000)
                                clicked = True
                                break
                        except Exception:
                            pass
                    if not clicked:
                        # Último recurso: clicar no elemento que contém o email
                        try:
                            page.get_by_text(TARGET_EMAIL).first.click()
                            page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass

                # Passo 2: página de senha (conta desconectada)
                if "signin" in page.url or "password" in page.url or "identifier" in page.url:
                    page.screenshot(path="oauth_debug.png")
                    ctx.close()
                    raise Exception(
                        f"YouTube OAuth: precisa fazer login com {TARGET_EMAIL}. "
                        "Rode login_youtube.py com o Chrome fechado para salvar a sessão correta."
                    )

                # Passo 3: botão Permitir / Allow
                clicou = False
                for sel in [
                    'button:has-text("Permitir")',
                    'button:has-text("Allow")',
                    '#submit_approve_access',
                    'button[value="true"]',
                ]:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            loc.first.click()
                            clicou = True
                            try:
                                page.wait_for_url(f"http://localhost:{port}/**", timeout=20000)
                            except Exception:
                                pass
                            break
                    except Exception:
                        pass
                if clicou:
                    break
                page.wait_for_timeout(2000)

            else:
                page.screenshot(path="oauth_debug.png")
                ctx.close()
                raise Exception(
                    "YouTube OAuth: botão Permitir não encontrado após 3 tentativas. "
                    "Veja oauth_debug.png para diagnóstico."
                )

            callback["done"].wait(timeout=20)
            ctx.close()
    finally:
        server.shutdown()

    if not callback["code"]:
        raise Exception("YouTube OAuth: timeout aguardando callback. Verifique oauth_debug.png.")

    flow.fetch_token(code=callback["code"])
    log("  YouTube OAuth: token renovado automaticamente via Playwright.")
    return flow.credentials


def get_youtube_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log(f"  YouTube: refresh inválido ({e}). Renovando via Playwright...")
                creds = None
        if not creds or not creds.valid:
            creds = _oauth_youtube_via_playwright()
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


def upload_via_youtube_api(caminho_video: str, titulo: str, descricao: str,
                           horario: datetime.datetime = None) -> str:
    """Faz upload para o YouTube via Data API v3 (sem browser)."""
    youtube = get_youtube_service()

    # Se o horário já passou, empurra para o mesmo horário do próximo dia
    agora = datetime.datetime.now()
    if horario and horario < agora:
        horario = horario + datetime.timedelta(days=1)

    # Horário de Brasília = UTC-3
    if horario:
        publish_at = (horario + datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        privacy = "private"  # obrigatório para agendamento
    else:
        publish_at = None
        privacy = "public"

    body = {
        "snippet": {
            "title": titulo[:100],
            "description": descricao,
            "tags": ["chicarminiveiculos", "miniveiculo", "quadriciclo", "shorts"],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(caminho_video, chunksize=10 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            log(f"  Upload: {int(status.progress() * 100)}%")

    video_id = response.get("id", "desconhecido")
    if publish_at:
        log(f"  Agendado para {horario.strftime('%d/%m/%Y %H:%M')} → youtube.com/shorts/{video_id}")
    else:
        log(f"  Publicado → youtube.com/shorts/{video_id}")
    return video_id




# ─────────────────────────────────────────
#  IA — TÍTULO E DESCRIÇÃO (otimizados para busca / Google AI)
# ─────────────────────────────────────────

SITE_BASE = "https://www.chicarminiveiculos.com"

# Modelos do catálogo do site — usado para detectar o modelo na legenda
# e linkar a página específica na descrição (palavras-chave em minúsculas).
MODELOS_CATALOGO = {
    "wolf 700":      ("Wolf 700 Mud",    f"{SITE_BASE}/catalogo/quadriciclos/wolf-700-mud"),
    "wolf 1000":     ("Wolf 1000",       f"{SITE_BASE}/catalogo/quadriciclos/wolf-1000"),
    "wolf 550":      ("Wolf 550",        f"{SITE_BASE}/catalogo/quadriciclos/wolf-550"),
    # Nomes COMPLETOS (nome + número): chave solta linkava modelo errado
    # (ex.: legenda "Fox 250" casava com "fox" → página do Fox 325)
    "farmer 300":    ("Farmer 300",      f"{SITE_BASE}/catalogo/quadriciclos/farmer-300"),
    "dakar 300":     ("Dakar 300",       f"{SITE_BASE}/catalogo/quadriciclos/dakar-300"),
    "fox 325":       ("Fox 325",         f"{SITE_BASE}/catalogo/quadriciclos/fox-325"),
    "bronco 200":    ("Bronco 200",      f"{SITE_BASE}/catalogo/buggys/bronco-200"),
    "macan 200":     ("Macan 200",       f"{SITE_BASE}/catalogo/buggys/macan-200"),
    "shark 1200":    ("Shark 1200",      f"{SITE_BASE}/catalogo/buggys/shark-1200"),
    "pro racing 110": ("Pro Racing 110", f"{SITE_BASE}/catalogo/mini-motos/pro-racing-110"),
    "pro racing 125": ("Pro Racing 125", f"{SITE_BASE}/catalogo/mini-motos/pro-racing-125"),
}

# Artigos reais do blog para linkar na descrição (rotaciona)
BLOG_LINKS = [
    ("Guia: manutenção de quadriciclo", f"{SITE_BASE}/blog/manutencao-quadriciclo"),
    ("Guia: pneus de quadriciclo",      f"{SITE_BASE}/blog/pneus-quadriciclo"),
    ("Problemas comuns em quadriciclos", f"{SITE_BASE}/blog/problemas-quadriciclo"),
]


def detectar_modelo(texto: str):
    """Retorna (nome, url) do modelo citado no texto, ou None."""
    t = (texto or "").lower()
    # chaves mais longas primeiro para "wolf 700" ganhar de "wolf"
    for chave in sorted(MODELOS_CATALOGO, key=len, reverse=True):
        if chave in t:
            return MODELOS_CATALOGO[chave]
    return None


_FALLBACK_TITLES = [
    "Quanto custa um quadriciclo em BH? Veja na Chicar",
    "Qual o melhor mini veículo para começar? Conheça este",
    "Vale a pena comprar quadriciclo seminovo? Veja este modelo",
    "Onde comprar quadriciclo em Belo Horizonte? Chicar Mini Veículos",
    "Qual quadriciclo escolher para trilha? Esse é destaque",
    "Quanto custa um buggy? Confira este na Chicar BH",
    "Quadriciclo seminovo vale a pena? Veja o estado deste",
    "Qual mini veículo comprar para o sítio? Veja esta opção",
    "Onde encontrar quadriciclo com procedência em BH?",
    "Quadriciclo para iniciante: qual escolher? Veja este",
    "Quanto custa manter um quadriciclo? Conheça este modelo",
    "Buggy ou quadriciclo: qual escolher? Veja este em detalhes",
    "Qual o quadriciclo mais procurado em BH? Veja na Chicar",
    "Mini veículo para família: qual a melhor opção?",
    "Quadriciclo automático existe? Veja este na Chicar BH",
]


def _limpar_legenda(texto: str) -> str:
    """Remove hashtags, menções e linhas decorativas ('- - - -') da legenda,
    preservando o texto original."""
    linhas = []
    for ln in (texto or "").splitlines():
        ln = re.sub(r"#\S+", "", ln)          # hashtags
        ln = re.sub(r"@[\w.]+", "", ln)       # menções
        ln = ln.strip()
        if not ln or re.fullmatch(r"[-–—•.\s]+", ln):
            continue                           # linha só de traços/pontos
        linhas.append(ln)
    return "\n".join(linhas).strip()


def gerar_titulo_com_ia(legenda: str, shortcode: str = "") -> str:
    """Título do YouTube = primeira frase da LEGENDA ORIGINAL do Instagram
    (fonte da verdade — nada de inventar conteúdo que não está no vídeo)."""
    limpo = _limpar_legenda(legenda)
    if limpo:
        primeira = limpo.splitlines()[0].strip()
        # se a primeira linha for muito curta, agrega a próxima
        if len(primeira) < 20 and len(limpo.splitlines()) > 1:
            primeira = (primeira + " — " + limpo.splitlines()[1].strip()).strip(" —")
        if len(primeira) >= 12:
            return primeira[:97] + ("..." if len(primeira) > 97 else "")
    import random
    return random.choice(_FALLBACK_TITLES)


def gerar_descricao(legenda: str = "", shortcode: str = "") -> str:
    """Descrição = LEGENDA ORIGINAL do Instagram (fonte da verdade) + contatos +
    hashtags. Link de modelo específico foi removido: o detector por palavra-chave
    linkava modelo errado (ex.: legenda 'Fox 250' → página do Fox 325)."""
    corpo = _limpar_legenda(legenda)
    if not corpo:
        corpo = "Mini veículos e quadriciclos novos e seminovos na Chicar, em BH."

    return (
        corpo + "\n\n"
        + f"Catálogo completo com preços e fichas técnicas:\n👉 {SITE_BASE}/catalogo\n\n"
        + f"📲 Instagram: https://instagram.com/{INSTAGRAM_PERFIL}\n"
        + f"💬 WhatsApp: https://api.whatsapp.com/send?phone=5531993875483\n\n"
        + "#chicarminiveiculos #miniveiculo #quadriciclo #seminovos #shorts"
    )

# Hashtags fixas do TikTok do chicar: nicho + localização (BH) + distribuição + marca
_HASHTAGS_TIKTOK_CHICAR = (
    "#quadriciclo #buggy #trilha #4x4 #offroad #miniveiculo "
    "#bh #belohorizonte #minasgerais "
    "#fyp #parati #viral #chicarminiveiculos"
)


def _hashtag_modelo(nome: str) -> str:
    """'Wolf 700 Mud' -> '#wolf700' (junta as 2 primeiras palavras)."""
    import re
    partes = nome.lower().split()
    slug = re.sub(r"[^a-z0-9]", "", "".join(partes[:2]))
    return f"#{slug}" if slug else ""


def gerar_legenda_tiktok_chicar(legenda: str = "", shortcode: str = "") -> str:
    """Legenda do TikTok = LEGENDA ORIGINAL do Instagram (fonte da verdade) +
    CTA + hashtags. O texto de quem postou descreve o que realmente aparece
    no vídeo — nada de gancho inventado por IA que descola do conteúdo."""
    corpo = _limpar_legenda(legenda)
    if not corpo:
        corpo = "Mini veículos e quadriciclos na Chicar 🔥"
    # TikTok: limite 2200; reserva espaço p/ CTA + hashtags
    corpo = corpo[:1800]

    tags = _HASHTAGS_TIKTOK_CHICAR
    modelo = detectar_modelo(legenda)
    if modelo:
        htag = _hashtag_modelo(modelo[0])
        if htag and htag not in tags:
            tags = f"{htag} {tags}"

    cta = "📲 Chama no WhatsApp: (31) 99387-5483\n📍 Belo Horizonte/MG"
    return f"{corpo}\n\n{cta}\n\n{tags}"


def calcular_horarios(n: int, offset: int = 0):
    """Retorna n horários a partir do slot 'offset', dentro dos MAX_POR_DIA slots fixos do dia.
    Usar offset=ja_postados_hoje evita colisão com vídeos já agendados anteriormente."""
    hoje      = datetime.date.today()
    inicio    = datetime.datetime.combine(hoje, datetime.time(HORA_INICIO, 0))
    fim       = datetime.datetime.combine(hoje, datetime.time(HORA_FIM, 0))
    intervalo = (fim - inicio) / MAX_POR_DIA
    todos_slots = [inicio + intervalo * i for i in range(MAX_POR_DIA)]
    slots_disponiveis = todos_slots[offset:]
    if len(slots_disponiveis) < n:
        raise ValueError(
            f"Slots insuficientes: {len(slots_disponiveis)} disponíveis, {n} necessários. "
            f"Verifique MAX_POR_DIA ({MAX_POR_DIA}) e o número de vídeos agendados para hoje."
        )
    return slots_disponiveis[:n]


# ─────────────────────────────────────────
#  INSTAGRAM
# ─────────────────────────────────────────

ARQUIVO_SESSAO_IG = "sessao_instagram"


def sincronizar_cookies_navegador(L):
    """Puxa cookies frescos do Instagram de um navegador logado e injeta na sessão
    do instaloader. Tenta Firefox > Chrome > Edge (Firefox é o mais confiável: lê o
    banco direto, sem App-Bound Encryption nem admin). Mantém a sessão viva sem
    precisar de login por senha — que sempre cai em checkpoint.
    Retorna True se conseguiu um sessionid válido."""
    try:
        import browser_cookie3 as bc
    except Exception:
        return False

    fontes = [("Firefox", bc.firefox), ("Chrome", bc.chrome), ("Edge", bc.edge)]
    for nome, fn in fontes:
        try:
            cookies = {c.name: c.value for c in fn(domain_name="instagram.com")}
        except Exception:
            continue  # navegador ausente, fechado ou cookies criptografados (Chrome 127+)
        if cookies.get("sessionid") and cookies.get("ds_user_id"):
            jar = L.context._session.cookies
            jar.clear()
            for nome_c, val in cookies.items():
                jar.set(nome_c, val, domain=".instagram.com")
            L.context.username = INSTAGRAM_LOGIN
            try:
                L.save_session_to_file(ARQUIVO_SESSAO_IG)
            except Exception:
                pass
            log(f"Cookies do Instagram sincronizados do {nome}.")
            return True
    return False


def login_instagram():
    L = instaloader.Instaloader(
        download_videos=True, download_video_thumbnails=False,
        download_geotags=False, download_comments=False,
        save_metadata=False, post_metadata_txt_pattern="",
        filename_pattern="{shortcode}", dirname_pattern=PASTA_DOWNLOAD
    )
    sessao_valida = False
    sessao_carregada = False

    # 1) Carrega a sessão salva como baseline
    try:
        L.load_session_from_file(INSTAGRAM_LOGIN, ARQUIVO_SESSAO_IG)
        sessao_carregada = True
    except Exception:
        log("Sessão Instagram salva não encontrada.")

    # 2) Refresca com cookies de um navegador logado (mantém a sessão viva sozinho)
    if sincronizar_cookies_navegador(L):
        sessao_carregada = True

    # 3) Valida a sessão atual no endpoint de feed
    try:
        r = L.context._session.get(
            "https://i.instagram.com/api/v1/feed/user/33469782306/",
            params={"count": 1},
            timeout=10,
        )
        if r.status_code == 200:
            sessao_valida = True
            log("Sessão Instagram validada.")
        else:
            log(f"Sessão Instagram expirada (status {r.status_code}).")
    except Exception as e:
        log(f"Falha ao validar sessão Instagram: {e}")

    # 4) Último recurso: login por senha (normalmente cai em checkpoint)
    if not sessao_valida:
        ultimo_erro = None
        for tentativa in range(1, 4):  # 3 tentativas
            try:
                L.login(INSTAGRAM_LOGIN, INSTAGRAM_SENHA)
                L.save_session_to_file(ARQUIVO_SESSAO_IG)
                log("Login Instagram realizado e sessão salva.")
                ultimo_erro = None
                sessao_valida = True
                break
            except SystemExit:
                ultimo_erro = "checkpoint / 2FA"
                break
            except Exception as e:
                ultimo_erro = e
                espera = tentativa * 30
                log(f"  Login Instagram falhou (tentativa {tentativa}/3): {e}. Aguardando {espera}s...")
                time.sleep(espera)

        if not sessao_valida:
            if sessao_carregada:
                # Sessão carregada mas re-login falhou — tenta usar a sessão existente mesmo assim
                log(f"  Re-login falhou ({ultimo_erro}). Tentando continuar com sessão existente...")
            else:
                raise Exception(
                    f"Instagram sem sessão válida ({ultimo_erro}). "
                    "Logue no @chicarminiveiculos no Firefox (ou rode renovar_sessao_instagram.py)."
                )

    # Usa a sessão interna do instaloader (já tem todos os cookies corretos)
    session = L.context._session
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-ig-app-id": IG_APP_ID,
    })
    return L, session

def buscar_perfil_e_reels(session, ja_conhecidos):
    """Faz UMA chamada a web_profile_info e retorna (user_id, lista_de_novos)."""
    DATA_MINIMA = datetime.datetime(2026, 4, 26)

    for tentativa in range(3):
        r = session.get(
            "https://i.instagram.com/api/v1/users/web_profile_info/",
            params={"username": INSTAGRAM_PERFIL},
            timeout=15,
        )
        if r.status_code == 429:
            espera = 30 * (tentativa + 1)
            log(f"  Instagram rate limit (429). Aguardando {espera}s...")
            time.sleep(espera)
            continue
        if r.status_code != 200:
            raise Exception(f"Instagram retornou {r.status_code}: {r.text[:200]}")
        break
    else:
        raise Exception("Instagram bloqueou após 3 tentativas.")

    user_data = r.json().get("data", {}).get("user", {})
    user_id   = user_data.get("id", "desconhecido")
    novos     = []

    # Reels ficam em edge_felix_video_timeline; posts gerais em edge_owner_to_timeline_media
    edges = []
    for key in ("edge_felix_video_timeline", "edge_owner_to_timeline_media"):
        edges += user_data.get(key, {}).get("edges", [])

    for edge in edges:
        node     = edge.get("node", {})
        typename = node.get("__typename", "")
        taken_at = datetime.datetime.fromtimestamp(node.get("taken_at_timestamp", 0))
        if taken_at < DATA_MINIMA:
            continue
        if typename not in ("GraphVideo", "GraphSidecar"):
            continue
        shortcode = node.get("shortcode", "")
        if not shortcode or shortcode in ja_conhecidos:
            continue

        # Tenta pegar video_url (pode não vir no profile_info — usamos shortcode para baixar)
        video_url = node.get("video_url", "")
        if not video_url:
            for child in node.get("edge_sidecar_to_children", {}).get("edges", []):
                v = child.get("node", {}).get("video_url", "")
                if v:
                    video_url = v
                    break

        if not video_url:
            # Busca a URL real via endpoint de mídia individual
            try:
                rm = session.get(
                    f"https://i.instagram.com/api/v1/media/{node.get('id')}/info/",
                    timeout=15,
                )
                if rm.status_code == 200:
                    items = rm.json().get("items", [])
                    if items and "video_versions" in items[0]:
                        video_url = items[0]["video_versions"][0]["url"]
            except Exception:
                pass

        if not video_url:
            continue

        legenda = (node.get("edge_media_to_caption", {})
                   .get("edges", [{}])[0]
                   .get("node", {})
                   .get("text", ""))
        novos.append({
            "shortcode": shortcode,
            "video_url": video_url,
            "legenda":   legenda,
            "taken_at":  node.get("taken_at_timestamp", 0),
        })

    return user_id, novos

def _carregar_token_instagram():
    _base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(_base, ARQUIVO_TOKEN_IG), encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["access_token"], cfg["ig_user_id"]


_FB_CACHE = {"page_id": None, "page_token": None, "videos": None}


def _normaliza_legenda(texto):
    """Normaliza legenda para casar o mesmo vídeo entre Instagram e Facebook."""
    t = re.sub(r"[#@]\S+", " ", texto or "")
    t = re.sub(r"[^0-9a-zA-ZáéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _facebook_page():
    """Retorna (page_id, page_token) da Página ligada à conta. Cacheado."""
    if _FB_CACHE["page_token"]:
        return _FB_CACHE["page_id"], _FB_CACHE["page_token"]
    token, _ = _carregar_token_instagram()
    r = requests.get(f"https://graph.facebook.com/{GRAPH_API_VER}/me/accounts",
                     params={"access_token": token, "fields": "id,access_token"}, timeout=30)
    dados = r.json().get("data", [])
    if not dados:
        raise Exception("Nenhuma Página do Facebook acessível pelo token.")
    _FB_CACHE["page_id"] = dados[0]["id"]
    _FB_CACHE["page_token"] = dados[0]["access_token"]
    return _FB_CACHE["page_id"], _FB_CACHE["page_token"]


def buscar_video_no_facebook(legenda):
    """Reels com música licenciada não expõem media_url na API do Instagram, mas a
    MESMA mídia está publicada na Página do Facebook.
    Devolve {'fb_url': permalink do reel, 'source': MP4 direto} ou None.

    IMPORTANTE: o campo 'source' entrega um stream de VÍDEO SEM ÁUDIO e em
    resolução menor (720p). Preferir sempre 'fb_url' com yt-dlp mesclando
    vídeo+áudio (chega a 1080p com som). 'source' fica só como último recurso."""
    try:
        page_id, page_token = _facebook_page()
    except Exception as e:
        log(f"  Facebook indisponível ({e}).")
        return None

    if _FB_CACHE["videos"] is None:
        videos = []
        for edge in ("videos", "video_reels"):
            try:
                r = requests.get(f"https://graph.facebook.com/{GRAPH_API_VER}/{page_id}/{edge}",
                                 params={"access_token": page_token,
                                         "fields": "id,description,title,source,created_time",
                                         "limit": 50}, timeout=30)
                videos += r.json().get("data", [])
            except Exception:
                pass
        _FB_CACHE["videos"] = videos

    alvo = _normaliza_legenda(legenda)
    if len(alvo) < 12:
        return None
    for v in _FB_CACHE["videos"]:
        cand = _normaliza_legenda(v.get("description") or v.get("title") or "")
        if not cand:
            continue
        # casa pelo início da legenda (o texto é o mesmo nas duas redes)
        n = min(len(alvo), len(cand), 40)
        if n >= 12 and alvo[:n] == cand[:n]:
            fbid = v.get("id")
            return {
                "fb_url": f"https://www.facebook.com/reel/{fbid}/" if fbid else None,
                "source": v.get("source"),
            }
    return None


def _shortcode_de_permalink(permalink):
    """Extrai o shortcode de uma permalink do Instagram (.../reel/CODE/ ou .../p/CODE/)."""
    m = re.search(r"/(?:reel|reels|p|tv)/([^/]+)/", permalink or "")
    return m.group(1) if m else ""


def buscar_reels_graph_api(ja_conhecidos):
    """Lê os reels da conta pela Instagram Graph API oficial usando o token permanente.
    Sem cookies, sem login, sem navegador. Retorna (ig_user_id, lista_de_novos).
    Cada novo inclui 'media_url' (link direto do .mp4) para download sem autenticação."""
    DATA_MINIMA = datetime.datetime(2026, 4, 26)
    token, igid = _carregar_token_instagram()
    base = f"https://graph.facebook.com/{GRAPH_API_VER}"
    campos = "id,caption,media_type,media_product_type,media_url,permalink,timestamp"

    novos = []
    url = f"{base}/{igid}/media"
    params = {"fields": campos, "limit": 25, "access_token": token}

    paginas = 0
    while url and paginas < 4:  # varre até ~100 itens mais recentes
        for tentativa in range(1, 4):
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                break
            espera = 30 * tentativa
            log(f"  Graph API status {r.status_code} (tentativa {tentativa}/3). Aguardando {espera}s...")
            time.sleep(espera)
        else:
            raise Exception(f"Graph API falhou: {r.status_code} {r.text[:200]}")

        data = r.json()
        parar = False
        for m in data.get("data", []):
            ts = m.get("timestamp", "")
            try:
                taken = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                taken = datetime.datetime.now()
            if taken < DATA_MINIMA:
                parar = True  # itens vêm do mais novo ao mais antigo
                break
            if m.get("media_type") != "VIDEO":
                continue
            media_url = m.get("media_url")
            shortcode = _shortcode_de_permalink(m.get("permalink"))
            if not shortcode or shortcode in ja_conhecidos:
                continue
            fb_url = None
            if not media_url:
                # Reel com música licenciada: a API do Instagram omite o media_url.
                # A mesma mídia está na Página do Facebook — baixamos de lá COM áudio.
                fb = buscar_video_no_facebook(m.get("caption", ""))
                if fb:
                    fb_url = fb.get("fb_url")
                    media_url = fb.get("source")  # só como último recurso (mudo/720p)
                    log(f"  [{shortcode}] media_url bloqueado (música) → baixando via Facebook.")
                else:
                    log(f"  [{shortcode}] media_url bloqueado e sem equivalente no Facebook → tentando yt-dlp.")
            novos.append({
                "shortcode": shortcode,
                "video_url": media_url or f"https://www.instagram.com/reel/{shortcode}/",
                "media_url": media_url,
                "fb_url":    fb_url,
                "legenda":   m.get("caption", "") or "",
                "taken_at":  int(taken.timestamp()),
            })

        if parar:
            break
        url = data.get("paging", {}).get("next")
        params = None  # a URL 'next' já carrega todos os parâmetros
        paginas += 1

    return igid, novos


def buscar_fotos_graph_api(ja_conhecidos, dias=30):
    """Busca fotos e carrosséis (IMAGE / CAROUSEL_ALBUM) dos últimos `dias` dias.
    Retorna lista de {shortcode, imagens:[urls], legenda, taken_at}.
    Carrosséis: só os filhos de imagem (vídeo em carrossel é ignorado aqui)."""
    token, igid = _carregar_token_instagram()
    base = f"https://graph.facebook.com/{GRAPH_API_VER}"
    data_minima = datetime.datetime.now() - datetime.timedelta(days=dias)
    campos = ("id,caption,media_type,media_url,permalink,timestamp,"
              "children{media_type,media_url}")

    novos = []
    url = f"{base}/{igid}/media"
    params = {"fields": campos, "limit": 25, "access_token": token}
    paginas = 0
    while url and paginas < 6:
        for tentativa in range(1, 4):
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                break
            time.sleep(30 * tentativa)
        else:
            raise Exception(f"Graph API fotos falhou: {r.status_code} {r.text[:200]}")

        data = r.json()
        parar = False
        for m in data.get("data", []):
            try:
                taken = datetime.datetime.strptime(m.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                continue
            if taken < data_minima:
                parar = True
                break
            tipo = m.get("media_type")
            if tipo not in ("IMAGE", "CAROUSEL_ALBUM"):
                continue
            shortcode = _shortcode_de_permalink(m.get("permalink"))
            if not shortcode or shortcode in ja_conhecidos:
                continue
            if tipo == "IMAGE":
                imagens = [m["media_url"]] if m.get("media_url") else []
            else:
                imagens = [c["media_url"] for c in m.get("children", {}).get("data", [])
                           if c.get("media_type") == "IMAGE" and c.get("media_url")]
            imagens = imagens[:10]  # limite multiphoto do Publer/TikTok
            if not imagens:
                continue
            novos.append({
                "shortcode": shortcode,
                "imagens":   imagens,
                "legenda":   m.get("caption", "") or "",
                "taken_at":  int(taken.timestamp()),
            })
        if parar:
            break
        url = data.get("paging", {}).get("next")
        params = None
        paginas += 1

    novos.reverse()  # mais antigos primeiro (ordem cronológica de postagem)
    return novos


def baixar_imagens(shortcode, urls):
    """Baixa as imagens de um post/carrossel. Retorna lista de caminhos locais."""
    os.makedirs(PASTA_DOWNLOAD, exist_ok=True)
    caminhos = []
    for i, u in enumerate(urls):
        caminho = os.path.join(PASTA_DOWNLOAD, f"{shortcode}_{i}.jpg")
        for tentativa in range(1, 4):
            try:
                r = requests.get(u, timeout=60)
                r.raise_for_status()
                with open(caminho, "wb") as fh:
                    fh.write(r.content)
                caminhos.append(caminho)
                break
            except Exception as e:
                if tentativa < 3:
                    time.sleep(10 * tentativa)
                else:
                    log(f"  Imagem {i} falhou ({e}) — seguindo sem ela.")
    return caminhos


def upload_fotos_via_publer(caminhos, texto, horario=None):
    """Posta foto única ou carrossel (Photo Mode) no TikTok via Publer."""
    headers_base = {
        "Authorization": f"Bearer-API {PUBLER_API_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE_ID,
    }
    media_ids = []
    for caminho in caminhos:
        resp = None
        ultimo_erro = None
        for tentativa in range(1, 4):
            try:
                with open(caminho, "rb") as f:
                    resp = requests.post(
                        f"{PUBLER_BASE_URL}/media", headers=headers_base,
                        files={"file": (os.path.basename(caminho), f, "image/jpeg")},
                        data={"direct_upload": "true"}, timeout=120,
                    )
                break
            except requests.exceptions.RequestException as e:
                ultimo_erro = e
                if tentativa < 3:
                    time.sleep(15 * tentativa)
        if resp is None or resp.status_code != 200 or not resp.json().get("id"):
            raise Exception(f"Publer upload de imagem falhou: {ultimo_erro or resp.text[:200]}")
        media_ids.append({"id": resp.json()["id"]})
    log(f"  Publer: {len(media_ids)} imagem(ns) enviada(s)")

    agora = datetime.datetime.now()
    if horario and horario < agora:
        horario = horario + datetime.timedelta(days=1)
    scheduled_at = (horario + datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if horario else None

    account_entry = {"id": PUBLER_TIKTOK_ID}
    if scheduled_at:
        account_entry["scheduled_at"] = scheduled_at
    body = {"bulk": {"state": "scheduled" if scheduled_at else "published", "posts": [{
        "networks": {"tiktok": {
            "type": "photo", "text": texto[:2200], "media": media_ids,
            "details": {"privacy_level": "PUBLIC_TO_EVERYONE", "disable_comment": False},
        }},
        "accounts": [account_entry],
    }]}}

    resp2 = None
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp2 = requests.post(f"{PUBLER_BASE_URL}/posts/schedule",
                                  headers={**headers_base, "Content-Type": "application/json"},
                                  json=body, timeout=60)
            break
        except requests.exceptions.RequestException as e:
            ultimo_erro = e
            if tentativa < 3:
                time.sleep(15 * tentativa)
    if resp2 is None:
        raise Exception(f"Publer schedule foto falhou: {ultimo_erro}")
    if resp2.status_code not in (200, 201):
        raise Exception(f"Publer schedule foto erro {resp2.status_code}: {resp2.text[:300]}")
    job_id = resp2.json().get("job_id", "desconhecido")
    log(f"  Publer foto agendada para {horario.strftime('%d/%m/%Y %H:%M') if horario else 'agora'} | job_id: {job_id}")
    return job_id


def buscar_reels_novos_ytdlp(ja_conhecidos):
    """Lista novos reels via yt-dlp.
    Usa instagram_cookies.txt se disponível (necessário no GitHub Actions),
    caso contrário tenta acesso público."""
    DATA_MINIMA = datetime.datetime(2026, 4, 26)

    # yt-dlp suporta o perfil principal — /reels/ não tem extractor
    url = f"https://www.instagram.com/{INSTAGRAM_PERFIL}/"
    _base = os.path.dirname(os.path.abspath(__file__))
    cookies_file = os.path.join(_base, "instagram_cookies.txt")

    ydl_opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "playlistend": 25,  # verifica os últimos 25 posts/reels
    }
    if os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file
        log("  yt-dlp: usando instagram_cookies.txt")

    novos = []
    user_id = "desconhecido"

    for tentativa in range(1, 4):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            user_id = (info.get("channel_id") or info.get("uploader_id", "desconhecido"))
            entries = info.get("entries") or []

            for entry in entries:
                shortcode = entry.get("id") or ""
                if not shortcode or shortcode in ja_conhecidos:
                    continue
                timestamp = entry.get("timestamp") or 0
                if timestamp:
                    taken_at = datetime.datetime.fromtimestamp(timestamp)
                    if taken_at < DATA_MINIMA:
                        continue
                legenda = entry.get("description") or entry.get("title") or ""
                novos.append({
                    "shortcode": shortcode,
                    "video_url": entry.get("url") or f"https://www.instagram.com/reel/{shortcode}/",
                    "legenda":   legenda,
                    "taken_at":  timestamp,
                })
            break  # sucesso
        except Exception as e:
            if tentativa < 3:
                espera = 30 * tentativa
                log(f"  yt-dlp listing falhou (tentativa {tentativa}/3): {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                raise Exception(f"Falha ao listar reels após 3 tentativas: {e}")

    return user_id, novos


_FFMPEG_DIR = None


def _ffmpeg_dir():
    """Diretório contendo 'ffmpeg' com o nome padrão, exigido pelo yt-dlp para
    mesclar vídeo+áudio. O binário do imageio-ffmpeg tem nome versionado."""
    global _FFMPEG_DIR
    if _FFMPEG_DIR:
        return _FFMPEG_DIR
    import shutil
    import imageio_ffmpeg
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ffmpeg")
    os.makedirs(destino, exist_ok=True)
    alvo = os.path.join(destino, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not os.path.exists(alvo):
        shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), alvo)
    _FFMPEG_DIR = destino
    return destino


def _tem_audio(caminho):
    """True se o arquivo tem faixa de áudio (guarda contra upload mudo)."""
    try:
        import subprocess
        import imageio_ffmpeg
        saida = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", caminho],
                               capture_output=True, text=True, errors="replace").stderr
        return "Audio:" in saida
    except Exception:
        return True  # na dúvida, não bloqueia o fluxo


def _baixar_com_ytdlp(url, shortcode, tentativas=3):
    """Baixa mesclando melhor vídeo + melhor áudio. Retorna o caminho ou None."""
    opts = {
        "outtmpl": os.path.join(PASTA_DOWNLOAD, f"{shortcode}.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_dir(),
        "quiet": True,
        "no_warnings": True,
    }
    for tentativa in range(1, tentativas + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            for ext in ("mp4", "mkv", "webm"):
                p = os.path.join(PASTA_DOWNLOAD, f"{shortcode}.{ext}")
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    return p
        except Exception as e:
            if tentativa < tentativas:
                espera = 20 * tentativa
                log(f"  yt-dlp falhou (tentativa {tentativa}/{tentativas}): {str(e)[:120]}. Aguardando {espera}s...")
                time.sleep(espera)
    return None


def baixar_video_instagram(shortcode, media_url=None, tentativas=3, fb_url=None):
    """Baixa o reel. Ordem de preferência:
    1) fb_url (reel na Página do Facebook) via yt-dlp mesclando vídeo+áudio — usado
       nos reels com música licenciada; entrega até 1080p COM som.
    2) media_url da Graph API do Instagram (download direto do CDN).
    3) yt-dlp no link público do Instagram."""
    caminho = os.path.join(PASTA_DOWNLOAD, f"{shortcode}.mp4")
    if os.path.exists(caminho):
        return caminho

    # 1) Reel com música: baixa do Facebook com áudio (o 'source' da API vem mudo)
    if fb_url:
        p = _baixar_com_ytdlp(fb_url, shortcode, tentativas)
        if p:
            if not _tem_audio(p):
                log(f"  ⚠️  {shortcode}: vídeo do Facebook veio SEM áudio.")
            if p != caminho:
                os.replace(p, caminho)
            return caminho
        log("  Facebook via yt-dlp falhou; tentando demais fontes...")

    # 2) Download direto do media_url da Graph API
    if media_url:
        for tentativa in range(1, tentativas + 1):
            try:
                with requests.get(media_url, stream=True, timeout=120) as resp:
                    resp.raise_for_status()
                    with open(caminho, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            if chunk:
                                fh.write(chunk)
                if os.path.getsize(caminho) > 0:
                    return caminho
            except Exception as e:
                if os.path.exists(caminho):
                    os.remove(caminho)
                if tentativa < tentativas:
                    espera = 15 * tentativa
                    log(f"  Download direto falhou (tentativa {tentativa}/{tentativas}): {e}. Aguardando {espera}s...")
                    time.sleep(espera)
        log("  Download direto falhou; tentando yt-dlp como fallback...")

    url = f"https://www.instagram.com/reel/{shortcode}/"
    opts = {
        "outtmpl": os.path.join(PASTA_DOWNLOAD, f"{shortcode}.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        # Sem credenciais: conta pública, yt-dlp acessa via scraping sem auth
    }
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            ultimo_erro = None
            break
        except Exception as e:
            ultimo_erro = e
            if tentativa < tentativas:
                espera = 30 * tentativa  # 30s, 60s, 90s
                log(f"  Download falhou (tentativa {tentativa}/{tentativas}). Aguardando {espera}s antes de tentar novamente...")
                time.sleep(espera)
    if ultimo_erro:
        raise ultimo_erro
    # yt-dlp pode salvar com extensão diferente; procura o arquivo
    for ext in ("mp4", "mkv", "webm"):
        p = os.path.join(PASTA_DOWNLOAD, f"{shortcode}.{ext}")
        if os.path.exists(p):
            if p != caminho:
                os.rename(p, caminho)
            return caminho
    raise Exception(f"Arquivo baixado não encontrado para {shortcode}")


# ─────────────────────────────────────────
#  FLUXO PRINCIPAL
# ─────────────────────────────────────────

_ARQUIVO_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente.lock")

def _adquirir_lock():
    """Evita duas execuções simultâneas (causa posts duplicados).
    Lock por arquivo com PID; considerado obsoleto após 2h."""
    if os.path.exists(_ARQUIVO_LOCK):
        idade = time.time() - os.path.getmtime(_ARQUIVO_LOCK)
        if idade < 2 * 3600:
            log("Outra execução em andamento (agente.lock). Abortando esta.")
            return False
        # lock velho — provável processo morto
    with open(_ARQUIVO_LOCK, "w") as f:
        f.write(str(os.getpid()))
    return True


def _liberar_lock():
    try:
        os.remove(_ARQUIVO_LOCK)
    except Exception:
        pass


def main():
    if not _adquirir_lock():
        return
    try:
        _main_protegido()
    finally:
        _liberar_lock()


def _main_protegido():
    log("=" * 50)
    log("AGENTE INICIADO")
    log(f"Perfil: @{INSTAGRAM_PERFIL}")

    os.makedirs(PASTA_DOWNLOAD, exist_ok=True)
    historico = carregar_historico()

    # ── VERIFICA CREDENCIAIS YOUTUBE ─────────────────────
    if not os.path.exists(CLIENT_SECRETS):
        log("ERRO: client_secrets.json não encontrado.")
        return
    if not os.path.exists(TOKEN_FILE):
        log("AVISO: token_youtube.pickle não encontrado — será necessário autorizar via browser.")
    log("Credenciais YouTube verificadas.")

    # ── FILA LEGADA: migra itens antigos de fila_reels.json ──────────────────
    fila_legada = carregar_fila()
    ja_conhecidos = shortcodes_postados(historico) | {f["shortcode"] for f in fila_legada}

    # ── BUSCA REELS NOVOS NO INSTAGRAM ───────────────────────────────────────
    log("Verificando novos Reels no Instagram...")
    novos_instagram = []
    user_id = "desconhecido"
    obtido = False

    # Método 0: Graph API oficial (preferido — token permanente, sem cookies/login)
    try:
        user_id, novos_instagram = buscar_reels_graph_api(ja_conhecidos)
        log(f"Graph API OK. Conta {user_id}: {len(novos_instagram)} reel(s) novos.")
        obtido = True
    except Exception as e:
        log(f"  Graph API indisponível ({e}). Tentando métodos legados (instaloader/yt-dlp)...")

    # Fallback: login instaloader → API privada, depois yt-dlp público
    if not obtido:
        log("Conectando ao Instagram (fallback)...")
        try:
            ig_loader, ig_session = login_instagram()
            log("Instagram OK (sessão).")
        except Exception as e:
            ig_session = None
            log(f"  Instagram: sessão indisponível ({e}). Usando acesso público via yt-dlp.")

        if ig_session is not None:
            try:
                user_id, novos_instagram = buscar_perfil_e_reels(ig_session, ja_conhecidos)
                log(f"Perfil encontrado. ID: {user_id}")
                obtido = True
            except Exception as e:
                log(f"  API Instagram falhou ({e}). Tentando via yt-dlp...")

        if not obtido:
            try:
                user_id, novos_instagram = buscar_reels_novos_ytdlp(ja_conhecidos)
                log(f"  yt-dlp: {len(novos_instagram)} reel(s) novos encontrados.")
            except Exception as e:
                log(f"ERRO ao buscar reels: {e}")
                return

    # Fila legada vem primeiro, novos do Instagram em seguida
    todos = fila_legada + novos_instagram

    # ── FOTOS E CARROSSÉIS (últimos 30 dias) → só TikTok (Photo Mode) ────────
    fotos_novas = []
    try:
        fotos_novas = buscar_fotos_graph_api(ja_conhecidos, dias=30)
        if fotos_novas:
            log(f"{len(fotos_novas)} foto(s)/carrossel(éis) novos para o TikTok.")
    except Exception as e:
        log(f"  Busca de fotos falhou ({e}). Seguindo só com reels.")

    if not todos and not fotos_novas:
        log("Nenhum Reel ou foto novos encontrados.")
        log("AGENTE FINALIZADO\n")
        _salvar_status_e_commitar()
        return

    if todos:
        log(f"{len(todos)} Reel(s) para processar "
            f"({len(fila_legada)} da fila legada + {len(novos_instagram)} novos).")

    # ── DOWNLOAD + UPLOAD IMEDIATO (just-in-time, mesmo dia) ──────────────────
    slot = None
    for reel in todos:
        shortcode = reel["shortcode"]
        legenda   = reel.get("legenda", "")
        slot      = proximo_slot_jit(slot)

        log(f"[{shortcode}] Slot: {slot.strftime('%d/%m/%Y %H:%M')}")

        # 1) Preparação (download + textos). Se falhar aqui, pula o reel.
        try:
            caminho = baixar_video_instagram(shortcode, reel.get("media_url"),
                                             fb_url=reel.get("fb_url"))
            time.sleep(2)
            titulo    = gerar_titulo_com_ia(legenda)
            descricao = gerar_descricao(legenda, shortcode)
            log(f"  Título: {titulo}")
            modelo = detectar_modelo(legenda)
            if modelo:
                log(f"  Modelo detectado: {modelo[0]} → link específico na descrição")
        except Exception as e:
            log(f"  ERRO ao preparar {shortcode}: {e}")
            continue

        # 2) YouTube e TikTok são INDEPENDENTES — a falha de um não impede o outro.
        video_id = None
        try:
            log(f"  Upload YouTube → {slot.strftime('%d/%m/%Y %H:%M')}...")
            video_id = upload_via_youtube_api(caminho, titulo, descricao, horario=slot)
            log(f"  ✅ YouTube OK! youtube.com/shorts/{video_id}")
        except Exception as e_yt:
            log(f"  ⚠️  YouTube ERRO: {e_yt}")

        tiktok_id = None
        try:
            log(f"  Upload TikTok/Publer → {slot.strftime('%d/%m/%Y %H:%M')}...")
            legenda_tiktok = gerar_legenda_tiktok_chicar(legenda, shortcode)
            log(f"  Legenda TikTok: {legenda_tiktok[:80].replace(chr(10), ' ')}")
            tiktok_id = upload_via_publer(caminho, legenda_tiktok, horario=slot)
            log(f"  ✅ TikTok/Publer OK! job_id: {tiktok_id}")
        except Exception as e_tt:
            log(f"  ⚠️  TikTok/Publer ERRO: {e_tt}")

        # 3) Registra só se ALGUM canal aceitou (evita repostar no que funcionou).
        #    Se ambos falharem, não registra → tenta de novo na próxima rodada.
        if video_id or tiktok_id:
            historico.append({
                "shortcode":        shortcode,
                "data":             slot.date().isoformat(),
                "horario_agendado": slot.strftime("%H:%M"),
                "youtube_id":       video_id,
                "tiktok_id":        tiktok_id,
            })
            salvar_historico(historico)
            fila_legada = [f for f in fila_legada if f["shortcode"] != shortcode]
            salvar_fila(fila_legada)
        else:
            log(f"  Nenhum canal aceitou {shortcode} — será tentado de novo na próxima rodada.")

        # 4) Limpa o arquivo local independentemente do resultado.
        try:
            if os.path.exists(caminho):
                os.remove(caminho)
                log("  Arquivo local removido.")
        except Exception:
            pass

    # ── FOTOS/CARROSSÉIS → TikTok Photo Mode (mesma lógica de legenda e JIT) ──
    for foto in fotos_novas:
        shortcode = foto["shortcode"]
        legenda   = foto.get("legenda", "")
        slot_f    = proximo_slot_foto(historico)
        log(f"[{shortcode}] FOTO ({len(foto['imagens'])} img) Slot: {slot_f.strftime('%d/%m/%Y %H:%M')}")

        caminhos = []
        try:
            caminhos = baixar_imagens(shortcode, foto["imagens"])
            if not caminhos:
                log(f"  Nenhuma imagem baixada de {shortcode} — pulando.")
                continue
            legenda_tiktok = gerar_legenda_tiktok_chicar(legenda, shortcode)
            log(f"  Legenda TikTok: {legenda_tiktok[:80].replace(chr(10), ' ')}")
            tiktok_id = upload_fotos_via_publer(caminhos, legenda_tiktok, horario=slot_f)
            log(f"  ✅ TikTok foto OK! job_id: {tiktok_id}")
            historico.append({
                "shortcode":        shortcode,
                "data":             slot_f.date().isoformat(),
                "horario_agendado": slot_f.strftime("%H:%M"),
                "youtube_id":       None,
                "tiktok_id":        tiktok_id,
                "tipo":             "foto",
            })
            salvar_historico(historico)
        except Exception as e:
            log(f"  ⚠️  Foto {shortcode} ERRO: {e}")
        finally:
            for cp in caminhos:
                try:
                    os.remove(cp)
                except Exception:
                    pass

    log("AGENTE FINALIZADO COM SUCESSO")
    log("=" * 50 + "\n")

    # ── PERSISTE ESTADO PARA DASHBOARD REMOTO ────────────────────────────────
    _salvar_status_e_commitar()

def _salvar_status_e_commitar():
    """Escreve status_agente.json e faz git commit+push para o dashboard no Streamlit Cloud."""
    import subprocess as sp

    agora = datetime.datetime.now()
    # Roda a cada 2h dentro da janela; próxima execução estimada
    proximo = _clamp_janela(agora + datetime.timedelta(hours=2))

    status = {
        "ultima_execucao": agora.isoformat(timespec="seconds"),
        "proximo_agendado": proximo.isoformat(timespec="seconds"),
        "status": "ok",
    }
    status_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "status_agente.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    try:
        base = os.path.dirname(os.path.abspath(__file__))
        arquivos = [
            "reels_postados.json",
            "tiktok_retroativo.json",
            "fila_reels.json",
            "status_agente.json",
        ]
        sp.run(["git", "add"] + arquivos, cwd=base, timeout=30, check=True)
        sp.run(
            ["git", "commit", "-m", f"estado: {agora.strftime('%Y-%m-%d %H:%M')}"],
            cwd=base, timeout=30,
        )
        sp.run(["git", "push"], cwd=base, timeout=60, check=True)
        log("  GitHub: estado commitado e enviado.")
    except Exception as e:
        log(f"  GitHub: commit ignorado ({e}).")


if __name__ == "__main__":
    main()
