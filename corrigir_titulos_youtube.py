"""
Corrige os títulos dos vídeos do YouTube que ficaram com o título genérico idêntico.
Busca a legenda do Instagram para cada reel e gera um título único com IA.
"""
import sys, os, pickle, time
sys.stdout.reconfigure(encoding="utf-8")

# Carrega .env (força sobrescrever mesmo se já existir na sessão)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), encoding="utf-8") as _f:
    for _line in _f:
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

import requests
import anthropic
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

TOKEN_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_youtube.pickle")
CLIENT_SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_secrets.json")

TITULO_ERRADO = "Esse veículo vai te surpreender — vem ver!"

# Mapeamento shortcode → youtube_id dos 8 vídeos com título errado
VIDEOS = [
    ("DYW3UoJRhwl", "JLgZQNCbOZI"),
    ("DYWwCB3x6c4", "Y4msEEdzl34"),
    ("DYVLWWOxo-L", "J-yRNXl6P-Q"),
    ("DYUu-E6xeZ0", "WXGZwL1mEGI"),
    ("DYUsRbCkZik", "Ce2UswEZ5FY"),
    ("DYSi66pxJtG", "W4hoZHBxkl4"),
    ("DYSVWNDxpBU", "yLsD7QWgJ9I"),
    ("DYP_Nq5RQf1", "91WS85iS4z4"),
]

INSTAGRAM_TOKEN  = os.environ.get("INSTAGRAM_TOKEN", "")
INSTAGRAM_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "")

def buscar_legenda_instagram(shortcode: str) -> str:
    """Tenta buscar a legenda do reel via Graph API."""
    try:
        # A Graph API não aceita shortcode diretamente — usa o media_id se disponível
        # Alternativa: buscar via oEmbed (público, sem auth)
        url = f"https://graph.facebook.com/v19.0/instagram_oembed?url=https://www.instagram.com/reel/{shortcode}/&fields=title&access_token={INSTAGRAM_TOKEN}"
        resp = requests.get(url, timeout=10)
        if resp.ok:
            data = resp.json()
            title = data.get("title", "")
            if title:
                return title
    except Exception:
        pass

    # Fallback: busca via página pública (sem autenticação)
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(
            f"https://www.instagram.com/reel/{shortcode}/",
            headers=headers, timeout=15
        )
        if resp.ok:
            text = resp.text
            # Extrai og:description
            import re
            m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', text)
            if m:
                return m.group(1)
    except Exception:
        pass

    return ""

def gerar_titulo(legenda: str) -> str:
    cliente = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = f"""Você é especialista em copywriting para YouTube Shorts de uma loja de mini veículos e quadriciclos chamada Chicar Mini Veículos, em BH.

Crie UM título para YouTube Shorts com base na legenda abaixo.

Regras:
- Máximo 80 caracteres
- Sem números (sem datas, preços, anos, contadores)
- Desperte curiosidade ou desejo imediato de clicar
- Linguagem natural e empolgante para quem quer comprar mini veículo
- No máximo 1 emoji, só se fizer sentido
- Sem aspas

Legenda: {legenda[:500] if legenda else "Mini veículo seminovo disponível na Chicar"}

Responda APENAS com o título."""
    resp = cliente.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip().strip('"').strip("'")[:100]

def get_youtube():
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)

def atualizar_titulo_youtube(youtube, video_id: str, novo_titulo: str):
    # Primeiro busca os dados atuais do vídeo
    resp = youtube.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        print(f"  ERRO: vídeo {video_id} não encontrado.")
        return False
    snippet = items[0]["snippet"]
    titulo_atual = snippet.get("title", "")
    if titulo_atual != TITULO_ERRADO:
        print(f"  PULADO: título já foi atualizado para '{titulo_atual}'")
        return False
    snippet["title"] = novo_titulo
    youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
    return True

def main():
    print("Corrigindo títulos dos vídeos YouTube...")
    print()
    youtube = get_youtube()
    titulos_gerados = set()

    for shortcode, video_id in VIDEOS:
        print(f"[{shortcode}] → {video_id}")
        legenda = buscar_legenda_instagram(shortcode)
        print(f"  Legenda: {legenda[:80] or '(não encontrada)'}")

        titulo = gerar_titulo(legenda)

        # Garante unicidade: se gerou o mesmo, pede de novo com contexto extra
        tentativas = 0
        while titulo in titulos_gerados and tentativas < 3:
            titulo = gerar_titulo(legenda + f" (variação {tentativas+2})")
            tentativas += 1

        titulos_gerados.add(titulo)
        print(f"  Novo título: {titulo}")

        ok = atualizar_titulo_youtube(youtube, video_id, titulo)
        if ok:
            print(f"  ✅ Atualizado!")
        time.sleep(2)

    print()
    print("Concluído.")

if __name__ == "__main__":
    main()
