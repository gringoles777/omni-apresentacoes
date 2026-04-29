#!/usr/bin/env python3
"""
Atualiza leads-responderam-tentativa-conectar.html
Etapa: Tentativa de Conectar (stage 421) — SDR-Novo
Roda via GitHub Actions todo dia útil às 16:50 BRT (19:50 UTC)
"""

import json, os, re, urllib.request, time, sys
from datetime import datetime

CHATWOOT_URL = "https://conversas-web.server.omnihypnosis.com.br"
ACCOUNT_ID = "1"
CW_TOKEN = os.environ["CHATWOOT_TOKEN"]
PIPE_KEY = os.environ["PIPEDRIVE_API_KEY"]
STAGE_ID = 421  # Tentativa de Conectar
TODAY = datetime.utcnow().strftime("%d/%m/%Y")

# ── helpers ──────────────────────────────────────────────────────────────────

def cw_get(path):
    url = f"{CHATWOOT_URL}/api/v1/accounts/{ACCOUNT_ID}/{path}"
    req = urllib.request.Request(url, headers={"api_access_token": CW_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [WARN] Chatwoot {path}: {e}", file=sys.stderr)
        return {}

def pipe_get(path):
    url = f"https://api.pipedrive.com/v1/{path}&api_token={PIPE_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [WARN] Pipedrive {path}: {e}", file=sys.stderr)
        return {}

def normalize_phone(phone):
    p = re.sub(r"[^\d]", "", phone)
    if p.startswith("0"): p = p[1:]
    if not p.startswith("55") and len(p) <= 13: p = "55" + p
    return p

def format_phone(phone):
    d = re.sub(r"[^\d]", "", phone)
    if d.startswith("55"): d = d[2:]
    if len(d) == 11: return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10: return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d

def is_real(content):
    if not content: return False
    c = content.strip().lower()
    if len(c) <= 3: return False
    single = {"oi","olá","ola","ok","sim","nao","não","tá","ta","bom dia","boa tarde",
              "boa noite","hey","ei","yes","blz","beleza","👍","😊","🙂","tbm","tb","certo","rs","rsrs"}
    if c in single: return False
    auto = ["fora do horário","fora do horario","atendimento automático","atendimento automatico",
            "horário de atendimento","retornaremos em breve","em breve retornaremos",
            "obrigado pelo contato","agradecemos seu contato","horario de funcionamento",
            "agradecemos a sua mensagem","agradecemos sua mensagem","não estamos disponíveis",
            "nao estamos disponiveis","indisponível no momento","indisponivel no momento",
            "obrigada pelo seu contato","obrigado pelo seu contato","angel agradece",
            "seja muito bem vind"]
    return not any(kw in c for kw in auto)

def esc(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def pipe_link(deal_id): return f"https://app.pipedrive.com/deal/{deal_id}"
def cw_link(conv_id): return f"{CHATWOOT_URL}/app/accounts/{ACCOUNT_ID}/conversations/{conv_id}"

# ── passo 1: coletar deals do stage 421 ──────────────────────────────────────

print("Coletando deals do Pipedrive stage 421...")
stage_deals = {}
phone_to_deal = {}
start = 0
while True:
    d = pipe_get(f"deals?stage_id={STAGE_ID}&status=open&limit=100&start={start}")
    deals = d.get("data") or []
    for deal in deals:
        did = deal.get("id")
        person = deal.get("person_id") or {}
        phones = person.get("phone") or []
        phone_val = next((p["value"] for p in phones if p.get("value")), "")
        owner = deal.get("user_id") or {}
        stage_deals[did] = {
            "deal_id": did,
            "name": person.get("name", deal.get("title", "?")),
            "phone": phone_val,
            "owner": owner.get("name", ""),
            "owner_id": owner.get("id", 0),
        }
        if phone_val:
            phone_to_deal[normalize_phone(phone_val)] = did
    more = ((d.get("additional_data") or {}).get("pagination") or {}).get("more_items_in_collection", False)
    if not more or not deals: break
    start += 100
    time.sleep(0.2)

total_deals = len(stage_deals)
print(f"Total stage 421: {total_deals} deals")

# ── passo 2: varrer conversas do Chatwoot ────────────────────────────────────

INBOX_MAP = {18: "Layslla brenda", 16: "David Marques"}
matched = {}

for inbox_id, sdr_name in INBOX_MAP.items():
    print(f"Varrendo inbox {inbox_id} ({sdr_name})...")
    page = 1
    while True:
        resp = cw_get(f"conversations?inbox_id={inbox_id}&page={page}")
        data = resp.get("data") or {}
        convs = data.get("payload", []) if isinstance(data, dict) else []
        if not convs: break
        for conv in convs:
            conv_id = conv.get("id")
            sender = (conv.get("meta") or {}).get("sender") or {}
            ext_id_raw = (sender.get("custom_attributes") or {}).get("external_id")
            deal_id = None
            if ext_id_raw:
                try:
                    deal_id = int(ext_id_raw)
                    if deal_id not in stage_deals: deal_id = None
                except: pass
            if not deal_id:
                phone = sender.get("phone_number") or ""
                if phone: deal_id = phone_to_deal.get(normalize_phone(phone))
            if deal_id and deal_id not in matched:
                matched[deal_id] = {"conv_id": conv_id, "sdr": sdr_name}
        page += 1
        time.sleep(0.1)
        if page > 200: break

print(f"Conversas encontradas: {len(matched)}")

# ── passo 3: verificar mensagens ──────────────────────────────────────────────

print("Verificando mensagens...")
results = []

for deal_id, conv_info in matched.items():
    conv_id = conv_info["conv_id"]
    deal = stage_deals[deal_id]
    r = cw_get(f"conversations/{conv_id}/messages")
    msgs = r.get("payload") or []
    if isinstance(msgs, dict): msgs = msgs.get("messages") or []
    incoming = [m for m in msgs if m.get("message_type") == 0]
    best = None
    for m in sorted(incoming, key=lambda x: x.get("created_at") or 0):
        content = (m.get("content") or "").strip()
        if is_real(content): best = content
    if best:
        results.append({
            "deal_id": deal_id,
            "name": deal["name"],
            "phone": deal["phone"],
            "owner": deal["owner"],
            "owner_id": deal["owner_id"],
            "conv_id": conv_id,
            "best_msg": best,
        })
    time.sleep(0.15)

LAYSLLA_ID = 22894651
DAVID_ID = 20525394
layslla = sorted([r for r in results if r.get("owner_id") == LAYSLLA_ID], key=lambda x: x["deal_id"])
david   = sorted([r for r in results if r.get("owner_id") == DAVID_ID],   key=lambda x: x["deal_id"])
total = len(results)
print(f"Leads com resposta real: {total} (Layslla {len(layslla)}, David {len(david)})")

# ── passo 4: gerar HTML ───────────────────────────────────────────────────────

def build_rows(leads):
    rows = []
    for i, r in enumerate(leads, 1):
        name = esc(r["name"].title() if r["name"].isupper() else r["name"])
        phone = format_phone(r.get("phone", ""))
        msg = esc((r.get("best_msg") or "").replace("\n", " ").strip())
        rows.append(f"""          <tr>
            <td class="td-num">{i:02d}</td>
            <td class="td-name">{name}</td>
            <td class="td-phone">{phone}</td>
            <td class="td-links">
              <a class="link-btn link-pipe" href="{pipe_link(r['deal_id'])}" target="_blank">Pipedrive ↗</a>
              <a class="link-btn link-chat" href="{cw_link(r['conv_id'])}" target="_blank">Chatwoot ↗</a>
            </td>
            <td class="td-msg">{msg}</td>
          </tr>""")
    return "\n".join(rows)

layslla_rows = build_rows(layslla)
david_rows   = build_rows(david)

html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Leads com Resposta — Tentativa de Conectar · OMNI Brasil</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --gold:#DC9F42; --gold-15:rgba(220,159,66,0.15); --gold-20:rgba(220,159,66,0.20); --gold-30:rgba(220,159,66,0.30);
    --dark:#050505; --surface:#0e0e0e; --surface-2:#161616; --border:rgba(220,159,66,0.12);
    --cream:#F5F0E8; --cream-60:rgba(245,240,232,0.60); --cream-40:rgba(245,240,232,0.40);
    --teal:#2A9D8F; --teal-10:rgba(42,157,143,0.10); --teal-20:rgba(42,157,143,0.20);
    --purple:#9B6FD4; --purple-10:rgba(155,111,212,0.10); --purple-20:rgba(155,111,212,0.20);
  }}
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:var(--dark);color:var(--cream);font-family:'Inter',sans-serif;line-height:1.65}}
  .container{{max-width:1200px;margin:0 auto;padding:0 40px}}
  h1{{font-family:'DM Serif Display',serif;font-size:clamp(2rem,3.5vw,3rem);font-weight:400;letter-spacing:-0.02em;color:var(--cream);line-height:1.1}}
  h2{{font-family:'DM Serif Display',serif;font-size:1.6rem;font-weight:400;letter-spacing:-0.01em;color:var(--cream)}}
  .hero{{padding:72px 0 48px;border-bottom:1px solid var(--border)}}
  .hero-eyebrow{{display:flex;align-items:center;gap:12px;margin-bottom:20px}}
  .hero-eyebrow .dot{{width:6px;height:6px;background:var(--gold);border-radius:50%}}
  .label{{font-family:'IBM Plex Mono',monospace;font-size:0.6rem;font-weight:500;letter-spacing:0.1em;text-transform:uppercase;color:var(--gold)}}
  .hero-subtitle{{font-size:0.95rem;color:var(--cream-60);margin-top:10px}}
  .stats-section{{padding:48px 0;border-bottom:1px solid var(--border)}}
  .stats-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}}
  .stat-card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:28px 24px}}
  .stat-number{{font-family:'DM Serif Display',serif;font-size:2.8rem;color:var(--gold);line-height:1;margin-bottom:8px}}
  .stat-label{{font-size:0.8rem;color:var(--cream-60);font-family:'IBM Plex Mono',monospace;letter-spacing:0.05em;text-transform:uppercase}}
  .sdr-section{{padding:56px 0;border-bottom:1px solid var(--border)}}
  .sdr-section:last-of-type{{border-bottom:none}}
  .sdr-header{{display:flex;align-items:center;gap:16px;margin-bottom:28px}}
  .sdr-badge{{display:inline-flex;align-items:center;padding:6px 14px;border-radius:6px;font-size:0.75rem;font-family:'IBM Plex Mono',monospace;font-weight:500;letter-spacing:0.06em;text-transform:uppercase}}
  .badge-layslla{{background:var(--purple-10);border:1px solid var(--purple-20);color:var(--purple)}}
  .badge-david{{background:var(--teal-10);border:1px solid var(--teal-20);color:var(--teal)}}
  .sdr-count{{font-size:0.8rem;color:var(--cream-40);font-family:'IBM Plex Mono',monospace}}
  .table-wrap{{overflow-x:auto;border-radius:12px;border:1px solid var(--border)}}
  table{{width:100%;border-collapse:collapse}}
  thead{{background:var(--surface-2)}}
  th{{padding:14px 18px;text-align:left;font-family:'IBM Plex Mono',monospace;font-size:0.65rem;font-weight:500;letter-spacing:0.08em;text-transform:uppercase;color:var(--gold);border-bottom:1px solid var(--border);white-space:nowrap}}
  td{{padding:14px 18px;border-bottom:1px solid rgba(220,159,66,0.06);font-size:0.875rem;vertical-align:top}}
  tr:last-child td{{border-bottom:none}}
  tr{{background:var(--surface);transition:background 0.15s}}
  tr:hover{{background:var(--gold-15)}}
  .td-num{{color:var(--cream-40);font-family:'IBM Plex Mono',monospace;font-size:0.7rem;width:36px}}
  .td-name{{font-weight:600;color:var(--cream);white-space:nowrap;max-width:180px;overflow:hidden;text-overflow:ellipsis}}
  .td-phone{{font-family:'IBM Plex Mono',monospace;font-size:0.78rem;color:var(--cream-60);white-space:nowrap}}
  .link-btn{{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;font-size:0.75rem;font-family:'IBM Plex Mono',monospace;font-weight:500;text-decoration:none;transition:all 0.15s;white-space:nowrap}}
  .link-pipe{{background:var(--gold-15);border:1px solid var(--gold-30);color:var(--gold)}}
  .link-pipe:hover{{background:var(--gold-20)}}
  .link-chat{{background:var(--teal-10);border:1px solid var(--teal-20);color:var(--teal)}}
  .link-chat:hover{{background:var(--teal-20)}}
  .td-links{{display:flex;flex-direction:column;gap:6px;white-space:nowrap}}
  .td-msg{{color:var(--cream-60);font-size:0.83rem;max-width:420px;line-height:1.5}}
  .footer{{padding:40px 0;border-top:1px solid var(--border)}}
  .footer-text{{font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:var(--cream-40);letter-spacing:0.05em}}
  @media(max-width:768px){{.container{{padding:0 20px}}.stats-grid{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>

<div class="hero">
  <div class="container">
    <div class="hero-eyebrow"><div class="dot"></div><span class="label">SDR-Novo · Pipeline Comercial · OMNI Brasil</span></div>
    <h1>Leads com Resposta</h1>
    <p class="hero-subtitle">Etapa Tentativa de Conectar — leads que responderam com mensagem substancial (excluídos: automáticos, "oi" único e sem conteúdo) · Atualizado em {TODAY}</p>
  </div>
</div>

<div class="stats-section">
  <div class="container">
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-number">{total_deals}</div><div class="stat-label">Total na etapa</div></div>
      <div class="stat-card"><div class="stat-number">{total}</div><div class="stat-label">Com resposta real</div></div>
      <div class="stat-card"><div class="stat-number">{len(layslla)}</div><div class="stat-label">Layslla</div></div>
      <div class="stat-card"><div class="stat-number">{len(david)}</div><div class="stat-label">David</div></div>
    </div>
  </div>
</div>

<div class="sdr-section">
  <div class="container">
    <div class="sdr-header">
      <h2>Layslla Brenda</h2>
      <span class="sdr-badge badge-layslla">SDR</span>
      <span class="sdr-count">{len(layslla)} leads</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Nome</th><th>Telefone</th><th>Links</th><th>Mensagem recebida</th></tr></thead>
        <tbody>
{layslla_rows}
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="sdr-section">
  <div class="container">
    <div class="sdr-header">
      <h2>David Marques</h2>
      <span class="sdr-badge badge-david">SDR</span>
      <span class="sdr-count">{len(david)} leads</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Nome</th><th>Telefone</th><th>Links</th><th>Mensagem recebida</th></tr></thead>
        <tbody>
{david_rows}
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="footer">
  <div class="container">
    <p class="footer-text">OMNI Brasil · Uso interno · {total} leads com resposta real de {total_deals} na etapa Tentativa de Conectar · {TODAY}</p>
  </div>
</div>

</body>
</html>"""

output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leads-responderam-tentativa-conectar.html")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"HTML salvo em: {output_path}")
print(f"RESULTADO FINAL: {total} leads com resposta real ({len(layslla)} Layslla + {len(david)} David)")
