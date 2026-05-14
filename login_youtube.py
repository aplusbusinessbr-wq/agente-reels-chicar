"""
Execute este script UMA VEZ para fazer login no YouTube Studio e salvar a sessão.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
import os

SESSAO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessao_youtube")

def main():
    print("=" * 50)
    print("LOGIN YOUTUBE STUDIO")
    print("=" * 50)
    print()

    os.makedirs(SESSAO_DIR, exist_ok=True)

    with sync_playwright() as p:
        # Tenta usar Chrome instalado; se não tiver, usa Chromium com flags anti-detecção
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=SESSAO_DIR,
                channel="chrome",
                headless=False,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            print("Usando Chrome instalado.")
        except Exception:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=SESSAO_DIR,
                headless=False,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            print("Usando Chromium.")

        page = ctx.new_page()

        # Remove o flag navigator.webdriver que o Google detecta
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            page.goto("https://studio.youtube.com", timeout=30000)
        except Exception as e:
            print(f"Aviso: {e}")

        print()
        print("Navegador aberto!")
        print()
        print("  1. Faça login com aplusbusinessbr@gmail.com")
        print("  2. Troque para o canal CHICAR MINI VEÍCULOS")
        print("     (clique na foto de perfil → Trocar conta → Chicar)")
        print()
        print(">> Quando estiver no YouTube Studio do canal CHICAR,")
        print(">> volte aqui e pressione ENTER para salvar a sessão...")
        input()

        ctx.storage_state(path=os.path.join(SESSAO_DIR, "state.json"))
        print()
        print("Sessao salva com sucesso!")
        ctx.close()

    print("Pronto! Agora pode rodar o agente normalmente.")

if __name__ == "__main__":
    main()
