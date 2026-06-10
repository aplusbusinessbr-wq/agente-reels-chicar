"""
=============================================================
  AGENTE: Reels → YouTube Shorts
  Cliente: @chicarminiveiculos
  Horário: 11:59 AM diário — posts distribuídos entre 12h e 21h
=============================================================
"""

import os
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
HORA_INICIO        = 12
HORA_FIM           = 21
MAX_POR_DIA        = 3
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

    # 1) Upload do arquivo de mídia
    log("  Publer: enviando vídeo...")
    with open(caminho_video, "rb") as f:
        resp = requests.post(
            f"{PUBLER_BASE_URL}/media",
            headers=headers_base,
            files={"file": (os.path.basename(caminho_video), f, "video/mp4")},
            data={"direct_upload": "true"},
        )
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

    resp2 = requests.post(
        f"{PUBLER_BASE_URL}/posts/schedule",
        headers={**headers_base, "Content-Type": "application/json"},
        json=body,
    )
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
    "farmer":        ("Farmer 300",      f"{SITE_BASE}/catalogo/quadriciclos/farmer-300"),
    "dakar":         ("Dakar 300",       f"{SITE_BASE}/catalogo/quadriciclos/dakar-300"),
    "fox":           ("Fox 325",         f"{SITE_BASE}/catalogo/quadriciclos/fox-325"),
    "bronco":        ("Bronco 200",      f"{SITE_BASE}/catalogo/buggys/bronco-200"),
    "macan":         ("Macan 200",       f"{SITE_BASE}/catalogo/buggys/macan-200"),
    "shark":         ("Shark 1200",      f"{SITE_BASE}/catalogo/buggys/shark-1200"),
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


def gerar_titulo_com_ia(legenda: str, shortcode: str = "") -> str:
    modelo = detectar_modelo(legenda)
    modelo_info = f'O vídeo é sobre o modelo "{modelo[0]}".' if modelo else ""
    try:
        cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = f"""Você é especialista em SEO para YouTube Shorts de uma loja de mini veículos e quadriciclos chamada Chicar Mini Veículos, em BH.

Crie UM título para YouTube Shorts com base na legenda abaixo.

O título deve ser A PERGUNTA QUE AS PESSOAS PESQUISAM NO GOOGLE sobre esse veículo — o Google AI cita vídeos cujo título bate com a busca.

Exemplos do formato desejado:
- "Quanto custa o quadriciclo Wolf 700? Vale a pena em 2026?"
- "Qual o melhor buggy para trilha? Veja o Shark 1200"
- "Quadriciclo Farmer 300 é bom? Análise rápida"

Regras:
- Máximo 90 caracteres
- Formato de pergunta de busca real (quanto custa, vale a pena, qual o melhor, é bom, onde comprar)
- {modelo_info or "Se a legenda citar o modelo do veículo, use o nome exato no título."}
- Pode usar o nome/número do modelo (ex.: Wolf 700), mas NUNCA invente preço em reais
- Sem emoji, sem aspas

Legenda: {legenda[:500] if legenda else "Mini veículo seminovo disponível na Chicar"}

Responda APENAS com o título."""
        resp = cliente.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip().strip('"').strip("'")[:100]
    except Exception as e:
        import random
        log(f"  Aviso: IA indisponível ({e}). Usando título alternativo.")
        return random.choice(_FALLBACK_TITLES)


def gerar_descricao(legenda: str = "", shortcode: str = "") -> str:
    """Descrição com link da página do modelo (quando detectado) + artigo do blog.
    Links específicos > link da home: o Google AI usa esses links como fonte."""
    modelo = detectar_modelo(legenda)

    if modelo:
        nome, url = modelo
        linha_modelo = f"{nome} disponível na Chicar Mini Veículos em BH.\n"
        linha_link = f"Preço atualizado e ficha técnica completa:\n👉 {url}\n\n"
    else:
        linha_modelo = "Mini veículos e quadriciclos novos e seminovos na Chicar, em BH.\n"
        linha_link = f"Catálogo completo com preços e fichas técnicas:\n👉 {SITE_BASE}/catalogo\n\n"

    # Rotaciona o artigo do blog de forma determinística por vídeo
    blog_titulo, blog_url = BLOG_LINKS[sum(ord(c) for c in (shortcode or "x")) % len(BLOG_LINKS)]

    return (
        linha_modelo
        + linha_link
        + f"{blog_titulo}:\n👉 {blog_url}\n\n"
        + f"📲 Instagram: https://instagram.com/{INSTAGRAM_PERFIL}\n"
        + f"💬 WhatsApp: https://api.whatsapp.com/send?phone=5531993875483\n\n"
        + "#chicarminiveiculos #miniveiculo #quadriciclo #seminovos #shorts"
    )

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

def login_instagram():
    L = instaloader.Instaloader(
        download_videos=True, download_video_thumbnails=False,
        download_geotags=False, download_comments=False,
        save_metadata=False, post_metadata_txt_pattern="",
        filename_pattern="{shortcode}", dirname_pattern=PASTA_DOWNLOAD
    )
    sessao_valida = False
    sessao_carregada = False
    try:
        L.load_session_from_file(INSTAGRAM_LOGIN, ARQUIVO_SESSAO_IG)
        sessao_carregada = True
        # Valida com o endpoint de feed
        r = L.context._session.get(
            "https://i.instagram.com/api/v1/feed/user/33469782306/",
            params={"count": 1},
            timeout=10,
        )
        if r.status_code == 200:
            sessao_valida = True
            log("Sessão Instagram carregada e validada.")
        else:
            log(f"Sessão Instagram expirada (status {r.status_code}). Tentando re-login...")
    except Exception:
        log("Sessão Instagram não encontrada. Tentando login...")

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
                raise Exception(
                    "Instagram bloqueou o login (checkpoint / 2FA). "
                    "Execute login_instagram.py manualmente e tente novamente."
                )
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
                raise Exception(f"Login Instagram: {ultimo_erro}")

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


def baixar_video_instagram(shortcode, tentativas=3):
    """Baixa reel do Instagram via yt-dlp com retry automático em caso de rate-limit."""
    caminho = os.path.join(PASTA_DOWNLOAD, f"{shortcode}.mp4")
    if os.path.exists(caminho):
        return caminho
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

def main():
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

    # ── LOGIN INSTAGRAM (opcional — fallback para sessão ou acesso público) ───
    log("Conectando ao Instagram...")
    try:
        ig_loader, ig_session = login_instagram()
        log("Instagram OK.")
    except Exception as e:
        ig_loader, ig_session = None, None
        log(f"  Instagram: sessão indisponível ({e}). Usando acesso público via yt-dlp.")

    # ── FILA LEGADA: migra itens antigos de fila_reels.json ──────────────────
    fila_legada = carregar_fila()
    ja_conhecidos = shortcodes_postados(historico) | {f["shortcode"] for f in fila_legada}

    # ── BUSCA REELS NOVOS NO INSTAGRAM ───────────────────────────────────────
    log("Verificando novos Reels no Instagram...")
    novos_instagram = []
    user_id = "desconhecido"

    # Método 1: API privada via sessão instaloader (preferido — legenda completa)
    if ig_session is not None:
        try:
            user_id, novos_instagram = buscar_perfil_e_reels(ig_session, ja_conhecidos)
            log(f"Perfil encontrado. ID: {user_id}")
        except Exception as e:
            log(f"  API Instagram falhou ({e}). Tentando via yt-dlp...")
            ig_session = None  # força fallback abaixo

    # Método 2: scraping público via yt-dlp (GitHub Actions, IP bloqueado, ou fallback)
    if ig_session is None:
        try:
            user_id, novos_instagram = buscar_reels_novos_ytdlp(ja_conhecidos)
            log(f"  yt-dlp: {len(novos_instagram)} reel(s) novos encontrados.")
        except Exception as e:
            log(f"ERRO ao buscar reels: {e}")
            return

    # Fila legada vem primeiro, novos do Instagram em seguida
    todos = fila_legada + novos_instagram

    if not todos:
        log("Nenhum Reel novo encontrado.")
        log("AGENTE FINALIZADO\n")
        return

    log(f"{len(todos)} Reel(s) para processar "
        f"({len(fila_legada)} da fila legada + {len(novos_instagram)} novos).")

    # ── DOWNLOAD + UPLOAD IMEDIATO ────────────────────────────────────────────
    for reel in todos:
        shortcode = reel["shortcode"]
        legenda   = reel.get("legenda", "")
        slot      = proximo_slot_disponivel(historico)

        log(f"[{shortcode}] Slot: {slot.strftime('%d/%m/%Y %H:%M')}")
        try:
            caminho = baixar_video_instagram(shortcode)
            time.sleep(2)

            titulo    = gerar_titulo_com_ia(legenda)
            descricao = gerar_descricao(legenda, shortcode)
            log(f"  Título: {titulo}")
            modelo = detectar_modelo(legenda)
            if modelo:
                log(f"  Modelo detectado: {modelo[0]} → link específico na descrição")

            log(f"  Upload YouTube → {slot.strftime('%d/%m/%Y %H:%M')}...")
            video_id = upload_via_youtube_api(caminho, titulo, descricao, horario=slot)
            log(f"  ✅ YouTube OK! youtube.com/shorts/{video_id}")

            tiktok_id = None
            try:
                log(f"  Upload TikTok/Publer → {slot.strftime('%d/%m/%Y %H:%M')}...")
                tiktok_id = upload_via_publer(caminho, titulo, horario=slot)
                log(f"  ✅ TikTok/Publer OK! job_id: {tiktok_id}")
            except Exception as e_tt:
                log(f"  ⚠️  TikTok/Publer ERRO (YouTube já foi): {e_tt}")

            historico.append({
                "shortcode":        shortcode,
                "data":             slot.date().isoformat(),
                "horario_agendado": slot.strftime("%H:%M"),
                "youtube_id":       video_id,
                "tiktok_id":        tiktok_id,
            })
            salvar_historico(historico)

            # Remove da fila legada se estava lá
            fila_legada = [f for f in fila_legada if f["shortcode"] != shortcode]
            salvar_fila(fila_legada)

            os.remove(caminho)
            log("  Arquivo local removido.")

        except Exception as e:
            log(f"  ERRO em {shortcode}: {e}")

    log("AGENTE FINALIZADO COM SUCESSO")
    log("=" * 50 + "\n")

    # ── PERSISTE ESTADO PARA DASHBOARD REMOTO ────────────────────────────────
    _salvar_status_e_commitar()

def _salvar_status_e_commitar():
    """Escreve status_agente.json e faz git commit+push para o dashboard no Streamlit Cloud."""
    import subprocess as sp

    agora = datetime.datetime.now()
    proximo = agora.replace(hour=11, minute=59, second=0, microsecond=0) + datetime.timedelta(days=1)

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
