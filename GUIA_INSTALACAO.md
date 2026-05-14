# 📋 GUIA DE INSTALAÇÃO — Agente Reels → YouTube Shorts
### Sem precisar entender programação

---

## ✅ PASSO 1 — Instalar o Python

1. Acesse: https://www.python.org/downloads/
2. Clique no botão amarelo **"Download Python 3.x.x"**
3. Execute o instalador baixado
4. ⚠️ **IMPORTANTE:** Na primeira tela, marque a opção **"Add Python to PATH"**
5. Clique em **"Install Now"**
6. Aguarde finalizar e clique em **"Close"**

**Verificar se instalou:** Abra o Prompt de Comando (tecle `Win + R`, digite `cmd`, Enter) e digite:
```
python --version
```
Se aparecer algo como `Python 3.12.0`, está correto. ✅

---

## ✅ PASSO 2 — Instalar as bibliotecas necessárias

1. Abra o Prompt de Comando (`Win + R` → `cmd` → Enter)
2. Cole o comando abaixo e pressione Enter:

```
pip install instaloader google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

3. Aguarde instalar tudo (pode demorar 1-2 minutos)
4. Quando voltar a piscar o cursor, está pronto ✅

---

## ✅ PASSO 3 — Criar as credenciais do YouTube (Google)

Esta etapa conecta o agente à sua conta do YouTube.

1. Acesse: https://console.cloud.google.com/
2. Faça login com a conta Google da agência
3. Clique em **"Selecionar projeto"** → **"Novo projeto"**
   - Nome: `Agente Reels Shorts`
   - Clique em **Criar**
4. No menu lateral, vá em **APIs e serviços** → **Biblioteca**
5. Pesquise por **"YouTube Data API v3"** → clique nela → clique em **Ativar**
6. Agora vá em **APIs e serviços** → **Credenciais**
7. Clique em **"+ Criar credenciais"** → **"ID do cliente OAuth"**
8. Em "Tipo de aplicativo", selecione **"App para computador"**
9. Nome: `Agente Reels` → clique em **Criar**
10. Clique em **"Baixar JSON"**
11. Renomeie o arquivo baixado para: `client_secrets.json`
12. Coloque esse arquivo na **mesma pasta** do script

---

## ✅ PASSO 4 — Configurar a pasta do agente

1. Crie uma pasta no seu computador, por exemplo: `C:\AgentReels`
2. Coloque dentro dessa pasta:
   - `reels_para_shorts.py`
   - `RODAR_AGENTE.bat`
   - `client_secrets.json` (baixado no Passo 3)

---

## ✅ PASSO 5 — Primeiro teste manual

1. Clique duas vezes no arquivo **`RODAR_AGENTE.bat`**
2. Na primeira vez, uma janela do Google vai abrir no navegador
3. Faça login com a conta da agência e clique em **"Permitir"**
4. O agente vai rodar e mostrar o progresso na tela
5. Verifique o arquivo `log_agente.txt` para ver o resultado

---

## ✅ PASSO 6 — Agendar para rodar todo dia às 11:59

1. Pressione `Win + R`, digite `taskschd.msc` e pressione Enter
2. No painel direito, clique em **"Criar Tarefa Básica"**
3. Nome: `Agente Reels Shorts` → clique em **Avançar**
4. Selecione **"Diariamente"** → clique em **Avançar**
5. Horário: **11:59:00** → clique em **Avançar**
6. Selecione **"Iniciar um programa"** → clique em **Avançar**
7. Em "Programa/script", clique em **Procurar** e selecione o arquivo `RODAR_AGENTE.bat`
8. Clique em **Avançar** → **Concluir**

🎉 Pronto! O agente vai rodar automaticamente todo dia às 11:59.

---

## 📁 Estrutura final da pasta

```
C:\AgentReels\
├── reels_para_shorts.py     ← script principal
├── RODAR_AGENTE.bat         ← executor
├── client_secrets.json      ← credenciais Google
├── token_youtube.pickle     ← gerado automaticamente na 1ª vez
├── reels_postados.json      ← histórico (gerado automaticamente)
├── log_agente.txt           ← log de todas as execuções
└── videos_baixados\         ← pasta temporária dos vídeos
```

---

## ❓ Problemas comuns

| Problema | Solução |
|----------|---------|
| `python não é reconhecido` | Reinstale o Python marcando "Add to PATH" |
| `ModuleNotFoundError` | Rode o pip install do Passo 2 novamente |
| Janela do Google não abre | Delete o arquivo `token_youtube.pickle` e rode novamente |
| Nenhum vídeo encontrado | Verifique se o perfil do Instagram é público |

---

## 💬 Precisa de ajuda?

Abra o arquivo `log_agente.txt`, copie o conteúdo e mande para o Claude — ele vai identificar o problema na hora.
