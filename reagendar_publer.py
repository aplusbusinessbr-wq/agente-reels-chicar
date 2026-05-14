"""
reagendar_publer.py
====================
Lê o tiktok_retroativo.json, redistribui todos os posts futuros
com 5 slots/dia (9h, 12h, 15h, 17h, 19h) a partir de amanhã
e atualiza cada agendamento via Publer API.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os, json, datetime, time
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reels_para_shorts import (
    PUBLER_API_KEY, PUBLER_WORKSPACE_ID, PUBLER_TIKTOK_ID,
    log,
)

ARQUIVO_RETRO = "tiktok_retroativo.json"
PUBLER_BASE_URL = "https://app.publer.com/api/v1"
SLOTS_DIARIOS = [9, 12, 15, 17, 19]


def carregar_retro():
    with open(ARQUIVO_RETRO, encoding="utf-8") as f:
        return json.load(f)

def salvar_retro(dados):
    with open(ARQUIVO_RETRO, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)


def calcular_slots(n: int, inicio: datetime.date):
    slots = []
    data = inicio
    while len(slots) < n:
        for hora in SLOTS_DIARIOS:
            slots.append(datetime.datetime.combine(data, datetime.time(hora, 0)))
            if len(slots) == n:
                break
        data += datetime.timedelta(days=1)
    return slots


def atualizar_post_publer(job_id: str, novo_horario: datetime.datetime) -> bool:
    """Atualiza o scheduled_at de um post existente no Publer."""
    headers = {
        "Authorization": f"Bearer-API {PUBLER_API_KEY}",
        "Publer-Workspace-Id": PUBLER_WORKSPACE_ID,
        "Content-Type": "application/json",
    }

    # Publer usa UTC internamente — converte BRT (UTC-3) → UTC
    horario_utc = novo_horario + datetime.timedelta(hours=3)
    scheduled_at = horario_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    body = {
        "post": {
            "scheduled_at": scheduled_at,
        }
    }

    r = requests.patch(
        f"{PUBLER_BASE_URL}/posts/{job_id}",
        headers=headers,
        json=body,
        timeout=20,
    )

    if r.status_code in (200, 201, 204):
        return True

    # Tenta PUT se PATCH não funcionar
    r2 = requests.put(
        f"{PUBLER_BASE_URL}/posts/{job_id}",
        headers=headers,
        json=body,
        timeout=20,
    )
    if r2.status_code in (200, 201, 204):
        return True

    log(f"    ⚠️  Erro ao atualizar {job_id}: PATCH={r.status_code} PUT={r2.status_code}")
    log(f"    Resposta: {r.text[:300]}")
    return False


def main():
    log("=" * 55)
    log("REAGENDAR PUBLER — 5 slots/dia (9h 12h 15h 17h 19h)")
    log("=" * 55)

    agora = datetime.datetime.now()
    amanha = agora.date() + datetime.timedelta(days=1)

    dados = carregar_retro()
    log(f"Total no JSON: {len(dados)} posts")

    # Separa passados (não mexe) de futuros (redistribui)
    passados = []
    futuros = []
    for item in dados:
        horario = datetime.datetime.fromisoformat(item["horario"])
        if horario <= agora:
            passados.append(item)
            log(f"  MANTÉM (já passou): {item['shortcode']} → {item['horario']}")
        else:
            futuros.append(item)

    log(f"\nPosts no passado (mantidos): {len(passados)}")
    log(f"Posts futuros a redistribuir: {len(futuros)}")

    if not futuros:
        log("Nenhum post futuro para redistribuir.")
        return

    # Ordena pelo taken_at para manter a ordem cronológica original
    futuros.sort(key=lambda x: x["taken_at"])

    # Gera novos slots a partir de amanhã
    novos_slots = calcular_slots(len(futuros), inicio=amanha)
    log(f"\nNovo primeiro slot: {novos_slots[0].strftime('%d/%m/%Y %H:%M')}")
    log(f"Novo último slot:   {novos_slots[-1].strftime('%d/%m/%Y %H:%M')}")
    log(f"Dias necessários:   {(novos_slots[-1].date() - amanha).days + 1}")
    log("")

    ok = 0
    erros = 0

    for i, item in enumerate(futuros):
        job_id = item["publer_job_id"]
        novo = novos_slots[i]
        antigo = item["horario"]

        log(f"[{i+1}/{len(futuros)}] {item['shortcode']}")
        log(f"    De: {antigo}")
        log(f"    Para: {novo.strftime('%d/%m/%Y %H:%M')}")

        if atualizar_post_publer(job_id, novo):
            item["horario"] = novo.isoformat()
            log(f"    ✅ Atualizado!")
            ok += 1
        else:
            log(f"    ❌ Falhou — horário original mantido no JSON.")
            erros += 1

        # Salva após cada update para não perder progresso
        salvar_retro(passados + futuros)
        time.sleep(0.5)

    log("\n" + "=" * 55)
    log(f"CONCLUÍDO! {ok} atualizados, {erros} erros.")
    log("=" * 55)


if __name__ == "__main__":
    main()
