"""
retroativo_tiktok.py
====================
Posta retroativamente no TikTok (via Publer) todos os Reels
a partir do shortcode de início definido em SHORTCODE_INICIO.
Agenda 4 por dia nos horários fixos: 09h, 12h, 17h, 19h.

Uso: python retroativo_tiktok.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os, json, time, datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reels_para_shorts import (
    INSTAGRAM_PERFIL,
    PASTA_DOWNLOAD, ARQUIVO_HISTORICO,
    login_instagram, baixar_video_instagram,
    gerar_titulo_com_ia, upload_via_publer,
    PUBLER_API_KEY, PUBLER_WORKSPACE_ID, PUBLER_TIKTOK_ID,
    log,
)

# ── Configurações ──────────────────────────────────────────────────────────────
SHORTCODE_INICIO = "DXnDTz3keTh"          # reel a partir do qual começa
SLOTS_DIARIOS    = [9, 12, 15, 17, 19]    # horários fixos (BRT)
ARQUIVO_RETRO    = "tiktok_retroativo.json"
PUBLER_BASE_URL  = "https://app.publer.com/api/v1"


# ── Persistência ───────────────────────────────────────────────────────────────

def carregar_retro():
    if os.path.exists(ARQUIVO_RETRO):
        with open(ARQUIVO_RETRO, encoding="utf-8") as f:
            return json.load(f)
    return []

def salvar_retro(dados):
    with open(ARQUIVO_RETRO, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

def shortcodes_ja_no_tiktok():
    """Retorna set de shortcodes que já têm tiktok via API antiga ou Publer."""
    ja = set()
    if os.path.exists(ARQUIVO_HISTORICO):
        with open(ARQUIVO_HISTORICO, encoding="utf-8") as f:
            for item in json.load(f):
                if item.get("tiktok_id"):
                    ja.add(item["shortcode"])
    for item in carregar_retro():
        if item.get("publer_job_id"):
            ja.add(item["shortcode"])
    return ja


# ── Publer helpers ─────────────────────────────────────────────────────────────

def publer_fila_atual():
    """Retorna quantos posts estão na fila do Publer para a conta TikTok."""
    headers = {
        "Authorization": f"Bearer-API {PUBLER_API_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE_ID,
    }
    r = requests.get(
        f"{PUBLER_BASE_URL}/posts",
        headers=headers,
        params={"account_id": PUBLER_TIKTOK_ID, "state": "scheduled"},
    )
    if r.status_code == 200:
        return r.json().get("total", 0)
    return 0


# ── Slots de horário ───────────────────────────────────────────────────────────

def calcular_slots(n_total: int, inicio: datetime.date):
    """Gera lista de datetimes para n_total vídeos, 4/dia nos horários fixos."""
    slots = []
    data  = inicio
    while len(slots) < n_total:
        for hora in SLOTS_DIARIOS:
            slots.append(datetime.datetime.combine(data, datetime.time(hora, 0)))
            if len(slots) == n_total:
                break
        data += datetime.timedelta(days=1)
    return slots


# ── Coleta de reels do Instagram ───────────────────────────────────────────────

def coletar_reels(ig_session, user_id: str, data_minima: datetime.datetime, ja_no_tiktok: set):
    """
    Itera posts do perfil via API mobile do Instagram (paginada) e retorna:
    {"shortcode", "legenda", "taken_at"} — somente vídeos novos.
    """
    reels       = []
    max_id      = None
    pagina      = 0
    parar       = False
    tentativas_401 = 0

    log(f"Iterando posts de @{INSTAGRAM_PERFIL} (user_id={user_id})...")

    while not parar:
        pagina += 1
        params = {"count": 12}
        if max_id:
            params["max_id"] = max_id

        r = ig_session.get(
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/",
            params=params,
            timeout=15,
        )
        if r.status_code == 429:
            log(f"  Rate-limit (429). Aguardando 60s...")
            time.sleep(60)
            pagina -= 1
            continue
        if r.status_code == 401:
            tentativas_401 += 1
            if tentativas_401 > 2:
                log(f"  401 persistente após {tentativas_401} tentativas. Parando.")
                break
            espera = 90 * tentativas_401
            log(f"  Sessão expirada (401) na página {pagina}. Aguardando {espera}s e refazendo login...")
            time.sleep(espera)
            # Força sessão fresca: apaga arquivo e refaz login com usuário/senha
            import os as _os
            arquivo_sessao = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "sessao_instagram")
            if _os.path.exists(arquivo_sessao):
                _os.remove(arquivo_sessao)
            _, ig_session = login_instagram()
            pagina -= 1
            continue
        if r.status_code != 200:
            log(f"  Erro {r.status_code} na página {pagina}. Parando.")
            break

        data     = r.json()
        items    = data.get("items", [])
        more     = data.get("more_available", False)
        max_id   = data.get("next_max_id")

        log(f"  Página {pagina}: {len(items)} posts")

        for item in items:
            taken = datetime.datetime.fromtimestamp(item.get("taken_at", 0))

            if taken < data_minima - datetime.timedelta(days=1):
                log(f"  Chegou ao limite ({taken.strftime('%d/%m/%Y')}). Parando.")
                parar = True
                break

            # Só vídeos (reels são media_type=2)
            if item.get("media_type") not in (2,):
                continue

            shortcode = item.get("code", "")
            if not shortcode or shortcode in ja_no_tiktok:
                if shortcode in ja_no_tiktok:
                    log(f"  {shortcode} ({taken.strftime('%d/%m/%Y')}) — já no TikTok. Pulando.")
                continue

            legenda = ""
            caption = item.get("caption")
            if caption:
                legenda = caption.get("text", "")

            reels.append({
                "shortcode": shortcode,
                "legenda":   legenda,
                "taken_at":  taken.isoformat(),
            })
            log(f"  + {shortcode} ({taken.strftime('%d/%m/%Y %H:%M')})")

        if not more or not max_id:
            break

        time.sleep(5)  # evita rate-limit entre páginas

    return reels


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log("=" * 50)
    log("RETROATIVO TIKTOK — via Publer")
    log(f"Início: {SHORTCODE_INICIO} | Slots: {SLOTS_DIARIOS}h")
    log("=" * 50)

    os.makedirs(PASTA_DOWNLOAD, exist_ok=True)

    # 1) Login Instagram
    log("Conectando ao Instagram...")
    ig_loader, ig_session = login_instagram()
    log("Instagram OK.")

    # 2) Data mínima: busca timestamp do post de referência via API
    log(f"Buscando data do post {SHORTCODE_INICIO}...")
    try:
        r = ig_session.get(
            f"https://i.instagram.com/api/v1/media/shortcode/{SHORTCODE_INICIO}/",
            timeout=15,
        )
        if r.status_code != 200:
            raise Exception(f"status {r.status_code}: {r.text[:200]}")
        item     = r.json().get("items", [{}])[0]
        user_id  = str(item.get("user", {}).get("pk", "33469782306"))
        taken_ts = item.get("taken_at", 0)
        data_min = datetime.datetime.fromtimestamp(taken_ts)
        log(f"Data mínima: {data_min.strftime('%d/%m/%Y %H:%M')} | user_id: {user_id}")
    except Exception as e:
        log(f"Aviso ao buscar post de referência ({e}). Usando user_id padrão e data_min manual.")
        user_id  = "33469782306"
        data_min = datetime.datetime(2026, 4, 27, 0, 0, 0)

    # 3) Coleta reels novos
    ja_no_tiktok = shortcodes_ja_no_tiktok()
    log(f"Shortcodes já no TikTok: {len(ja_no_tiktok)}")
    reels = coletar_reels(ig_session, user_id, data_min, ja_no_tiktok)

    if not reels:
        log("Nenhum reel novo para postar no TikTok.")
        return

    # Ordena do mais antigo para o mais novo
    reels.sort(key=lambda x: x["taken_at"])
    log(f"\nTotal a agendar: {len(reels)} reel(s)")

    # 4) Plano pago — sem limite de fila
    reels_nesta_rodada = reels
    log(f"Agendando todos os {len(reels_nesta_rodada)} reel(s) de uma vez (plano pago Publer).")

    # 5) Calcula slots a partir de hoje
    hoje  = datetime.date.today()
    slots = calcular_slots(len(reels_nesta_rodada), inicio=hoje)
    log(f"Primeiro slot: {slots[0].strftime('%d/%m/%Y %H:%M')}")
    log(f"Último slot:   {slots[-1].strftime('%d/%m/%Y %H:%M')}\n")

    # 6) Download + upload para Publer
    retro = carregar_retro()
    ok    = 0
    for i, reel in enumerate(reels_nesta_rodada):
        shortcode = reel["shortcode"]
        legenda   = reel["legenda"]
        horario   = slots[i]

        log(f"[{i+1}/{len(reels_nesta_rodada)}] {shortcode} → {horario.strftime('%d/%m/%Y %H:%M')}")
        try:
            caminho = baixar_video_instagram(shortcode)
            time.sleep(8)

            titulo = gerar_titulo_com_ia(legenda)
            log(f"  Título: {titulo}")

            job_id = upload_via_publer(caminho, titulo, horario=horario)
            log(f"  ✅ Publer OK! job_id: {job_id}")

            retro.append({
                "shortcode":     shortcode,
                "taken_at":      reel["taken_at"],
                "horario":       horario.isoformat(),
                "publer_job_id": job_id,
                "titulo":        titulo,
                "legenda":       legenda[:400],
            })
            salvar_retro(retro)
            os.remove(caminho)
            log("  Arquivo local removido.")
            ok += 1

        except Exception as e:
            log(f"  ERRO em {shortcode}: {e}")

    log("\n" + "=" * 50)
    log(f"CONCLUÍDO! {ok}/{len(reels_nesta_rodada)} agendados no Publer.")
    log("=" * 50)


if __name__ == "__main__":
    main()
