"""
renovar_sessao_instagram.py
===========================
Abre um browser para fazer login no Instagram e exporta os cookies para
instagram_cookies.txt (formato Netscape, usado pelo yt-dlp).

Execute este script sempre que o agente apresentar erros de sessão Instagram.
Após rodar, o script automaticamente atualiza o secret INSTAGRAM_COOKIES_B64 no GitHub.

Uso:
    python renovar_sessao_instagram.py
"""

import os
import sys
import base64
import subprocess
import json
import time

# ── carrega .env ────────────────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()
_load_env()

INSTAGRAM_LOGIN = os.environ.get("INSTAGRAM_LOGIN", "")
INSTAGRAM_SENHA = os.environ.get("INSTAGRAM_SENHA", "")
COOKIES_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instagram_cookies.txt")
GITHUB_REPO     = "aplusbusinessbr-wq/agente-reels-chicar"

def exportar_cookies_via_playwright():
    from playwright.sync_api import sync_playwright

    print("Abrindo browser para login no Instagram...")
    print(f"Conta: {INSTAGRAM_LOGIN}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=500)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()

        print("Navegando para instagram.com...")
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Aceitar cookies/consent se aparecer (vários textos possíveis)
        for consent_text in ["Allow all cookies", "Accept All", "Aceitar todos", "Aceitar tudo", "Allow essential and optional cookies"]:
            try:
                page.click(f"text={consent_text}", timeout=2000)
                time.sleep(1)
                break
            except Exception:
                pass

        # Vai para login se não estiver lá
        if "accounts/login" not in page.url and "instagram.com" in page.url:
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

        # Aceitar cookies novamente se aparecer
        for consent_text in ["Allow all cookies", "Accept All", "Aceitar todos", "Aceitar tudo"]:
            try:
                page.click(f"text={consent_text}", timeout=2000)
                time.sleep(1)
                break
            except Exception:
                pass

        # Preenche login — tenta vários seletores possíveis
        print("Preenchendo credenciais...")
        username_selectors = [
            'input[name="username"]',
            'input[aria-label="Phone number, username, or email"]',
            'input[aria-label*="username"]',
            'input[type="text"]',
        ]
        username_filled = False
        for sel in username_selectors:
            try:
                page.fill(sel, INSTAGRAM_LOGIN, timeout=5000)
                username_filled = True
                print(f"  Username preenchido via: {sel}")
                break
            except Exception:
                pass

        if not username_filled:
            print("AVISO: não encontrou campo de username. Aguardando 10s para interação manual...")
            time.sleep(10)

        time.sleep(0.5)
        password_selectors = [
            'input[name="password"]',
            'input[aria-label="Password"]',
            'input[type="password"]',
        ]
        for sel in password_selectors:
            try:
                page.fill(sel, INSTAGRAM_SENHA, timeout=5000)
                print(f"  Senha preenchida via: {sel}")
                break
            except Exception:
                pass

        time.sleep(0.5)
        try:
            page.click('button[type="submit"]', timeout=5000)
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        print("Aguardando login completar (pode pedir verificação)...")
        print("Se aparecer pedido de verificação/código, complete manualmente no browser.")

        # Espera até navegar para home ou feed (máx 60s para verificação manual)
        try:
            page.wait_for_url("**/instagram.com/**", timeout=60000)
            # Aguarda não estar mais na tela de login
            for _ in range(30):
                url = page.url
                if "accounts/login" not in url and "challenge" not in url:
                    break
                time.sleep(2)
        except Exception:
            pass

        current_url = page.url
        print(f"URL atual: {current_url}")

        if "instagram.com/accounts/login" in current_url:
            print("ERRO: ainda na tela de login. Verifique as credenciais.")
            browser.close()
            return False

        # Exporta cookies no formato Netscape
        print("Exportando cookies...")
        cookies = ctx.cookies()
        instagram_cookies = [c for c in cookies if "instagram.com" in c.get("domain", "")]

        lines = ["# Netscape HTTP Cookie File"]
        for c in instagram_cookies:
            domain = c["domain"]
            domain_flag = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", time.time() + 86400 * 365))
            path = c.get("path", "/")
            name = c["name"]
            value = c["value"]
            lines.append(f"{domain}\t{domain_flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")

        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        browser.close()

        # Verifica se o sessionid foi capturado
        sessionid_ok = any("sessionid" in line and len(line.split("\t")) > 6 and len(line.split("\t")[6]) > 10
                          for line in lines)
        if not sessionid_ok:
            print("AVISO: sessionid não encontrado nos cookies. O login pode não ter sido completo.")
            return False

        print(f"✅ {len(instagram_cookies)} cookies exportados para {COOKIES_FILE}")
        return True


def atualizar_secret_github(cookies_file):
    """Atualiza o secret INSTAGRAM_COOKIES_B64 no GitHub."""
    try:
        with open(cookies_file, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        result = subprocess.run(
            ["gh", "secret", "set", "INSTAGRAM_COOKIES_B64",
             "--repo", GITHUB_REPO,
             "--body", b64],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print("✅ Secret INSTAGRAM_COOKIES_B64 atualizado no GitHub.")
        else:
            print(f"ERRO ao atualizar secret: {result.stderr}")
    except Exception as e:
        print(f"ERRO ao atualizar secret: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("  RENOVAR SESSÃO INSTAGRAM")
    print("=" * 50)

    if not INSTAGRAM_LOGIN or not INSTAGRAM_SENHA:
        print("ERRO: INSTAGRAM_LOGIN ou INSTAGRAM_SENHA não definidos no .env")
        sys.exit(1)

    ok = exportar_cookies_via_playwright()
    if ok:
        atualizar_secret_github(COOKIES_FILE)
        print("\nPronto! O agente no GitHub Actions usará os novos cookies.")
        print("Se os cookies expirarem, rode este script novamente.")
    else:
        print("\nFalhou. Tente rodar novamente ou faça o login manualmente.")
        sys.exit(1)
