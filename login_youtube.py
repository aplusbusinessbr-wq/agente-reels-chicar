"""
Execute este script UMA VEZ para fazer login no YouTube Studio e salvar a sessão.
Usa o Chrome Default (que já tem bruna.chicar@gmail.com logada) para capturar os cookies.
"""
import sys
import os
import time
import threading
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

SESSAO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessao_youtube")
# Chrome Default = perfil do guigui, que já tem bruna.chicar@gmail.com logada
CHROME_USER_DATA = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")

def main():
    print("=" * 50)
    print("LOGIN YOUTUBE STUDIO")
    print("=" * 50)
    print()

    os.makedirs(SESSAO_DIR, exist_ok=True)

    with sync_playwright() as p:
        ctx = None
        modo = ""

        # Abre Chrome Default (tem bruna.chicar@gmail.com já logada)
        if os.path.exists(CHROME_USER_DATA):
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=CHROME_USER_DATA,
                    channel="chrome",
                    headless=False,
                    args=[
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"],
                    no_viewport=True,
                )
                modo = "Chrome Default (bruna.chicar@gmail.com)"
            except Exception as e:
                print(f"Chrome indisponível: {e}")

        if ctx is None:
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
            modo = "Chromium (sessão isolada — faça login manualmente)"

        print(f"Usando: {modo}")

        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            page.goto("https://studio.youtube.com", timeout=30000)
        except Exception as e:
            print(f"Aviso: {e}")

        print()
        print("Navegador aberto!")
        print()
        print("  Certifique-se de estar no YouTube Studio do canal CHICAR MINI VEÍCULOS")
        print("  logado com bruna.chicar@gmail.com (conta proprietária).")

        SINAL = os.path.join(SESSAO_DIR, ".salvar")
        if os.path.exists(SINAL):
            os.remove(SINAL)

        print()
        print(">> Quando estiver no YouTube Studio do canal CHICAR,")
        print(">> crie o arquivo: sessao_youtube\\.salvar")
        print(">> (ou pressione ENTER neste terminal)")
        print()

        salvo = threading.Event()

        def _watch():
            while not salvo.is_set():
                if os.path.exists(SINAL):
                    salvo.set()
                time.sleep(1)

        def _stdin():
            try:
                input()
                salvo.set()
            except Exception:
                pass

        threading.Thread(target=_watch, daemon=True).start()
        threading.Thread(target=_stdin, daemon=True).start()
        salvo.wait(timeout=300)

        ctx.storage_state(path=os.path.join(SESSAO_DIR, "state.json"))
        print()
        print("Sessao salva com sucesso!")
        ctx.close()

    print("Pronto! Agora pode rodar o agente normalmente.")

if __name__ == "__main__":
    main()
