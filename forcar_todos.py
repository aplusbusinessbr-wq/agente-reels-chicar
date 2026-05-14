"""
Forca o upload de TODOS os reels na fila de uma so vez,
respeitando as datas agendadas de cada um.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os, json, time, pickle, datetime
import yt_dlp
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
import anthropic

# ── Importa configurações do script principal ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reels_para_shorts import (
    ANTHROPIC_API_KEY, PASTA_DOWNLOAD, ARQUIVO_HISTORICO, ARQUIVO_FILA,
    HORA_INICIO, HORA_FIM, TOKEN_FILE, CLIENT_SECRETS, SCOPES,
    INSTAGRAM_LOGIN, INSTAGRAM_SENHA, INSTAGRAM_PERFIL,
    get_youtube_service, upload_via_youtube_api, upload_via_publer,
    gerar_titulo_com_ia, gerar_descricao,
    carregar_historico, salvar_historico, carregar_fila, salvar_fila,
    baixar_video_instagram, log, shortcodes_postados
)

MAX_POR_DIA = 3

def calcular_horarios_para_data(n: int, data: datetime.date, offset: int = 0):
    """Retorna n horários a partir do slot 'offset', dentro dos MAX_POR_DIA slots fixos do dia.
    offset = número de vídeos já agendados/postados nesse dia (evita colisão)."""
    inicio    = datetime.datetime.combine(data, datetime.time(HORA_INICIO, 0))
    fim       = datetime.datetime.combine(data, datetime.time(HORA_FIM, 0))
    intervalo = (fim - inicio) / MAX_POR_DIA
    todos_slots = [inicio + intervalo * i for i in range(MAX_POR_DIA)]
    slots_disponiveis = todos_slots[offset:]
    if len(slots_disponiveis) < n:
        raise ValueError(
            f"Slots insuficientes para {data}: {len(slots_disponiveis)} disponíveis, {n} necessários."
        )
    return slots_disponiveis[:n]

def main():
    log("=" * 50)
    log("FORCAR UPLOAD DE TODA A FILA")
    log("=" * 50)

    os.makedirs(PASTA_DOWNLOAD, exist_ok=True)
    historico = carregar_historico()
    fila      = carregar_fila()

    if not fila:
        log("Fila vazia. Nada a fazer.")
        return

    # Agrupa por data
    from collections import defaultdict
    por_data = defaultdict(list)
    for item in fila:
        por_data[item["data_agendada"]].append(item)

    log(f"Total na fila: {len(fila)} reel(s) em {len(por_data)} dia(s)")
    for d, items in sorted(por_data.items()):
        log(f"  {d}: {len(items)} reel(s)")

    descricao = gerar_descricao()

    for data_str, items in sorted(por_data.items()):
        data = datetime.date.fromisoformat(data_str)
        # Conta vídeos já registrados no histórico para esse dia (evita colisão de horário)
        ja_nesse_dia = sum(1 for h in historico if h.get("data") == data_str and h.get("youtube_id"))
        log(f"\n--- Processando {data_str} ({len(items)} reel(s), offset={ja_nesse_dia}) ---")
        try:
            horarios = calcular_horarios_para_data(len(items), data, offset=ja_nesse_dia)
        except ValueError as e:
            log(f"  AVISO: {e} Pulando esse dia.")
            continue

        for i, post in enumerate(items):
            shortcode = post["shortcode"]
            legenda   = post.get("legenda", "")
            horario   = horarios[i]

            log(f"Baixando: {shortcode}")
            try:
                caminho = baixar_video_instagram(shortcode)
                time.sleep(8)

                titulo = gerar_titulo_com_ia(legenda)
                log(f"Título: {titulo}")

                log(f"Upload YouTube agendado para {horario.strftime('%d/%m/%Y %H:%M')}...")
                video_id = upload_via_youtube_api(caminho, titulo, descricao, horario=horario)
                log(f"  ✅ YouTube OK! youtube.com/shorts/{video_id}")

                tiktok_id = None
                try:
                    log(f"Upload TikTok/Publer agendado para {horario.strftime('%d/%m/%Y %H:%M')}...")
                    tiktok_id = upload_via_publer(caminho, titulo, horario=horario)
                    log(f"  ✅ TikTok/Publer OK! job_id: {tiktok_id}")
                except Exception as e_tt:
                    log(f"  ⚠️  TikTok/Publer ERRO (YouTube já foi): {e_tt}")

                historico.append({"shortcode": shortcode, "data": data_str, "youtube_id": video_id, "tiktok_id": tiktok_id})
                salvar_historico(historico)
                fila = [x for x in fila if x["shortcode"] != shortcode]
                salvar_fila(fila)

                os.remove(caminho)
                log("  Arquivo local removido.")

            except Exception as e:
                log(f"  ERRO em {shortcode}: {e}")

    log("\n" + "=" * 50)
    log("CONCLUIDO!")
    fila_restante = carregar_fila()
    log(f"Reels restantes na fila: {len(fila_restante)}")

if __name__ == "__main__":
    main()
