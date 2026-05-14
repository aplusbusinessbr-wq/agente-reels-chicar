"""
Execute UMA VEZ para fazer login no TikTok e salvar o token de acesso.
Uso: python login_tiktok.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os, json, time, hashlib, secrets, webbrowser, urllib.parse, base64
import requests

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE  = os.path.join(BASE_DIR, "token_tiktok.json")

# ── Credenciais do app (Sandbox) ──────────────────────────────────────────────
CLIENT_KEY    = "sbaw3liviaqovs8uzv"
CLIENT_SECRET = "gdrGb6ikg2ZKgAmjVGGXHSXdsiVBDjSA"
REDIRECT_URI  = "https://agentereels.local/callback"
SCOPES        = "user.info.basic,video.publish,video.upload"
SANDBOX       = True

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def main():
    # Gera PKCE
    code_verifier     = secrets.token_urlsafe(64)
    digest            = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge    = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state             = secrets.token_urlsafe(16)

    params = {
        "client_key":            CLIENT_KEY,
        "response_type":         "code",
        "scope":                 SCOPES,
        "redirect_uri":          REDIRECT_URI,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    if SANDBOX:
        params["environment"] = "sandbox"

    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("=" * 60)
    print("  LOGIN TIKTOK — AgenteReels")
    print("=" * 60)
    print()
    print("1. O navegador vai abrir para autorizacao.")
    print("2. Faca login com a conta TikTok da CHICAR.")
    print("3. Apos autorizar, o navegador vai mostrar um ERRO de site")
    print("   (isso e normal — a URL ainda contem o codigo).")
    print("4. Copie a URL COMPLETA da barra de endereco do navegador")
    print("   e cole aqui no terminal.")
    print()
    input("Pressione ENTER para abrir o navegador...")
    webbrowser.open(url)

    print()
    print("Cole aqui a URL completa da barra de endereco apos autorizar:")
    redirect_url = input("> ").strip()

    # Extrai o code da URL colada
    parsed = urllib.parse.urlparse(redirect_url)
    params_resp = urllib.parse.parse_qs(parsed.query)

    if "error" in params_resp:
        print(f"ERRO retornado pelo TikTok: {params_resp['error']}")
        return

    auth_code = params_resp.get("code", [None])[0]
    if not auth_code:
        # Tenta extrair direto caso o usuario tenha colado so o code
        if "code=" in redirect_url:
            auth_code = redirect_url.split("code=")[1].split("&")[0]
        else:
            print("Codigo nao encontrado na URL. Tente novamente.")
            return

    print(f"Codigo recebido. Trocando por token...")

    resp = requests.post(TOKEN_URL, data={
        "client_key":    CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code":          auth_code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": code_verifier,
    })

    if resp.status_code != 200:
        print(f"ERRO ao trocar token: {resp.status_code}")
        print(resp.text)
        return

    data = resp.json()
    if data.get("error"):
        print(f"ERRO: {data}")
        return

    token = {
        "access_token":  data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "open_id":       data.get("open_id", ""),
        "expires_at":    int(time.time()) + int(data.get("expires_in", 86400)),
        "sandbox":       SANDBOX,
    }

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)

    print()
    print("Token salvo com sucesso!")
    print(f"  open_id : {token['open_id']}")
    print(f"  sandbox : {token['sandbox']}")
    print()
    print("Pronto! Agora pode rodar o agente normalmente.")

if __name__ == "__main__":
    main()
