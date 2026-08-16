"""Diagnóstico da integração com a Instagram Graph API (token permanente).
Não posta nada — apenas valida o token e lista os reels novos que o agente enxerga.
Uso:  python testar_instagram_graph.py
"""
import sys, io, os, json, datetime, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "https://graph.facebook.com/v21.0"

with open("token_instagram.json", encoding="utf-8") as f:
    cfg = json.load(f)
TOKEN, IGID = cfg["access_token"], cfg["ig_user_id"]

print("== 1) Validade do token ==")
d = requests.get(f"{BASE}/debug_token", params={"input_token": TOKEN, "access_token": TOKEN}, timeout=20).json().get("data", {})
exp = d.get("expires_at")
print("  tipo   :", d.get("type"))
print("  válido :", d.get("is_valid"))
print("  expira :", "NUNCA" if not exp else datetime.datetime.utcfromtimestamp(exp).strftime("%Y-%m-%d %H:%M UTC"))

print("\n== 2) Conta ==")
c = requests.get(f"{BASE}/{IGID}", params={"fields": "username,media_count,followers_count", "access_token": TOKEN}, timeout=20).json()
print(f"  @{c.get('username')} | {c.get('media_count')} mídias | {c.get('followers_count')} seguidores")

print("\n== 3) Últimos reels (VIDEO) ==")
r = requests.get(f"{BASE}/{IGID}/media", params={
    "fields": "caption,media_type,media_url,permalink,timestamp", "limit": 8, "access_token": TOKEN,
}, timeout=30).json()
for m in r.get("data", []):
    if m.get("media_type") != "VIDEO":
        continue
    cap = (m.get("caption") or "").replace("\n", " ")[:55]
    print(f"  {m.get('timestamp')} | media_url={'sim' if m.get('media_url') else 'NAO'} | {cap}")

print("\n✅ Integração Graph API operacional.")
