"""
Roda UMA VEZ para obter o token OAuth do YouTube.
Abre o seu Chrome real (já logado como bruna.chicar@gmail.com),
você clica em Permitir, e o token fica salvo em token_youtube.pickle.
"""
import sys
import os
import pickle
sys.stdout.reconfigure(encoding="utf-8")

CLIENT_SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_secrets.json")
TOKEN_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_youtube.pickle")
SCOPES         = ["https://www.googleapis.com/auth/youtube"]

def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("=" * 50)
    print("OBTER TOKEN YOUTUBE")
    print("=" * 50)
    print()
    print("Vai abrir o seu Chrome.")
    print("Selecione a conta: bruna.chicar@gmail.com")
    print("Clique em Permitir.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, scopes=SCOPES)
    # Abre o Chrome/browser padrão do sistema — já tem bruna.chicar logada
    creds = flow.run_local_server(port=0, open_browser=True)

    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)

    print()
    print(f"Token salvo em: {TOKEN_FILE}")
    print("Pronto! Agora pode rodar o agente normalmente.")

if __name__ == "__main__":
    main()
