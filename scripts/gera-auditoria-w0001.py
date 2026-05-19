#!/usr/bin/env python3
"""
Gera auditoria-fluxo-tentativa-conectar.html — snapshot completo do stage 421.
Classifica cada lead: respondeu / atrasado / D4-mover / normal / sem dados.
"""

import json, re, time, urllib.parse, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Constants ──────────────────────────────────────────────────────────────────
PIPEDRIVE_TOKEN = "fd0d8ec92950c2fc3053b6d126ce54a4a0e03d37"
CHATWOOT_BASE   = "https://conversas-web.server.omnihypnosis.com.br"
CHATWOOT_TOKEN  = "oLg7zw97T5TcomRRmfYrwecy"
CHATWOOT_ACCOUNT = "1"

PIPELINE_ID      = 65
STAGE_TENTATIVA  = 421

OWNER_DAVID   = 20525394
OWNER_LAYSLLA = 22894651
OWNER_RAFA    = 2872886
OWNER_NAMES   = {OWNER_DAVID: "David Marques", OWNER_LAYSLLA: "Layslla Brenda", OWNER_RAFA: "Rafaela Mendes"}
SDR_ORDER     = [OWNER_LAYSLLA, OWNER_DAVID, OWNER_RAFA]

BRT     = timezone(timedelta(hours=-3))
NOW_BRT = datetime.now(BRT)
NOW_TS  = int(NOW_BRT.timestamp())
AUDIT_TIMESTAMP = NOW_BRT.strftime("%d/%m/%Y às %H:%M (BRT)")

OUT_PATH = Path(__file__).parent.parent / "auditoria-fluxo-tentativa-conectar.html"

# ── HTTP ───────────────────────────────────────────────────────────────────────
def http_get(url, headers=None, retries=3, timeout=30):
    for attempt in range(retries):
        try:
            req = Request(url, headers=headers or {}, method="GET")
            with urlopen(req, timeout=timeout) as r:
                data = r.read()
                return json.loads(data.decode("utf-8")) if data else {}
        except HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 5)
            elif 500 <= e.code < 600:
                time.sleep(2 ** attempt)
            else:
                return {}
        except (URLError, Exception):
            time.sleep(2 ** attempt)
    return {}

def pipe_get(path):
    sep = "&" if "?" in path else "?"
    return http_get(f"https://api.pipedrive.com/v1/{path}{sep}api_token={PIPEDRIVE_TOKEN}")

def cw_get(path):
    return http_get(f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT}/{path}",
                    headers={"api_access_token": CHATWOOT_TOKEN})

# ── Pipedrive ──────────────────────────────────────────────────────────────────
def fetch_deals_421():
    print("Buscando deals stage 421...")
    all_deals, start = [], 0
    while True:
        resp = pipe_get(f"deals?stage_id={STAGE_TENTATIVA}&status=open&pipeline_id={PIPELINE_ID}&limit=500&start={start}")
        batch = resp.get("data") or []
        if not batch:
            break
        all_deals.extend(batch)
        pag = (resp.get("additional_data") or {}).get("pagination") or {}
        if pag.get("more_items_in_collection"):
            start = pag.get("next_start", start + len(batch))
        else:
            break
    filtered = [d for d in all_deals if d and d.get("status") == "open"]
    print(f"  {len(filtered)} deals encontrados")
    return filtered

def fetch_activities(deal_id):
    resp = pipe_get(f"activities?deal_id={deal_id}&type=whatsapp&limit=50&sort=id%20DESC")
    return resp.get("data") or []

# ── Chatwoot ───────────────────────────────────────────────────────────────────
def search_cw_contact(phone_raw):
    if not phone_raw:
        return None
    phone = re.sub(r"\D", "", phone_raw)
    if not phone:
        return None
    # Search by last 10 digits (more permissive match)
    q = urllib.parse.quote(phone[-10:])
    resp = cw_get(f"contacts/search?q={q}&include_contacts=true")
    payload = resp.get("payload") or []
    if isinstance(payload, list) and payload:
        return payload[0].get("id")
    return None

def fetch_cw_contact_convs(contact_id):
    if not contact_id:
        return []
    resp = cw_get(f"contacts/{contact_id}/conversations")
    p = resp.get("payload")
    if isinstance(p, dict):
        return p.get("payload") or []
    if isinstance(p, list):
        return p
    return []

def fetch_messages(conv_id):
    if not conv_id:
        return []
    resp = cw_get(f"conversations/{conv_id}/messages")
    p = resp.get("payload")
    if isinstance(p, list):
        return p
    return []

def get_best_conv(convs):
    """Retorna (conv_id, last_incoming_ts) do conv mais recente com mensagem."""
    best_conv = None
    best_ts   = 0
    for c in convs or []:
        cid = c.get("id")
        if not cid:
            continue
        msgs = fetch_messages(cid)
        time.sleep(0.05)
        for m in msgs:
            if m.get("message_type") == 0:  # incoming
                ts = m.get("created_at")
                if isinstance(ts, (int, float)) and int(ts) > best_ts:
                    best_ts   = int(ts)
                    best_conv = cid
    return best_conv, (best_ts if best_ts else None)

# ── Classification ─────────────────────────────────────────────────────────────
# SLA em horas calendário: após D{n}, próximo disparo esperado em até SLA_H horas
SLA_H = {1: 18, 2: 30, 3: 30}

def parse_pipe_ts(add_time):
    if not add_time:
        return 0
    try:
        return int(datetime.strptime(add_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0

def classify(activities, last_incoming_ts):
    """
    Retorna (category, last_d, last_d_ts)
    category: responded | atrasado | d4_move | normal | sem_disparos
    """
    last_d, last_d_ts = 0, None
    pat = re.compile(r"Disparo\s+([1-4])", re.IGNORECASE)
    for a in activities or []:
        m = pat.search(str(a.get("subject", "")))
        if m:
            dn = int(m.group(1))
            ts = parse_pipe_ts(a.get("add_time"))
            if dn > last_d or (dn == last_d and ts > (last_d_ts or 0)):
                last_d, last_d_ts = dn, ts

    responded_72h = bool(last_incoming_ts and last_incoming_ts > NOW_TS - 72 * 3600)

    if responded_72h:
        return "responded", last_d, last_d_ts
    if last_d == 4:
        return "d4_move", 4, last_d_ts
    if last_d in (1, 2, 3):
        elapsed_h = (NOW_TS - last_d_ts) / 3600 if last_d_ts else 9999
        if elapsed_h <= SLA_H[last_d]:
            return "normal", last_d, last_d_ts
        return "atrasado", last_d, last_d_ts
    return "sem_disparos", 0, None

# ── Helpers ────────────────────────────────────────────────────────────────────
def fmt_phone(raw):
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("55"): d = d[2:]
    if len(d) == 11: return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10: return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d or raw

def esc(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def days_ago_str(ts):
    if not ts: return "—"
    return f"{(NOW_TS - ts)/86400:.1f}d"

def fmt_ts(ts):
    if not ts: return "—"
    return datetime.fromtimestamp(ts, tz=BRT).strftime("%d/%m %H:%M")

D_STYLE = {
    0: ("rgba(136,136,136,0.15)", "rgba(136,136,136,0.3)", "#888"),
    1: ("rgba(42,157,143,0.13)", "rgba(42,157,143,0.27)", "#2A9D8F"),
    2: ("rgba(220,159,66,0.13)", "rgba(220,159,66,0.27)", "#DC9F42"),
    3: ("rgba(155,111,212,0.13)", "rgba(155,111,212,0.27)", "#9B6FD4"),
    4: ("rgba(224,82,82,0.13)", "rgba(224,82,82,0.27)", "#E05252"),
}

def d_badge(n):
    bg, bd, col = D_STYLE.get(n, D_STYLE[1])
    label = f"D{n}" if n else "—"
    return (f'<span style="background:{bg};border:1px solid {bd};color:{col};'
            f'padding:3px 10px;border-radius:6px;font-size:0.75rem;'
            f'font-family:\'IBM Plex Mono\',monospace;font-weight:600">{label}</span>')

def link_btns(deal_id, conv_id):
    pipe = (f'<a href="https://app.pipedrive.com/deal/{deal_id}" target="_blank" '
            f'style="display:inline-flex;align-items:center;padding:4px 10px;border-radius:5px;'
            f'font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace;font-weight:500;'
            f'text-decoration:none;background:rgba(220,159,66,0.15);'
            f'border:1px solid rgba(220,159,66,0.3);color:#DC9F42;margin-right:4px">Pipe ↗</a>')
    chat = ""
    if conv_id:
        chat = (f'<a href="{CHATWOOT_BASE}/app/accounts/{CHATWOOT_ACCOUNT}/conversations/{conv_id}" '
                f'target="_blank" style="display:inline-flex;align-items:center;padding:4px 10px;'
                f'border-radius:5px;font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace;'
                f'font-weight:500;text-decoration:none;background:rgba(42,157,143,0.10);'
                f'border:1px solid rgba(42,157,143,0.20);color:#2A9D8F">Chat ↗</a>')
    return f'<td style="white-space:nowrap">{pipe}{chat}</td>'

# ── Table row builder ──────────────────────────────────────────────────────────
def status_badge(cat, last_d, last_d_ts):
    if cat == "responded":
        return ('<span style="background:#0d2b1a;border:1px solid #4CAF6F33;color:#4CAF6F;'
                'padding:3px 10px;border-radius:6px;font-size:0.7rem;'
                'font-family:\'IBM Plex Mono\',monospace;white-space:nowrap">'
                'Respondeu — mover para Conectado</span>')
    if cat == "d4_move":
        return ('<span style="background:#1a0d2b;border:1px solid #9B6FD433;color:#9B6FD4;'
                'padding:3px 10px;border-radius:6px;font-size:0.7rem;'
                'font-family:\'IBM Plex Mono\',monospace;white-space:nowrap">'
                'D4 enviado — mover para Nutrição</span>')
    if cat == "normal":
        return (f'<span style="background:rgba(220,159,66,0.08);border:1px solid rgba(220,159,66,0.25);'
                f'color:#DC9F42;padding:3px 10px;border-radius:6px;font-size:0.7rem;'
                f'font-family:\'IBM Plex Mono\',monospace;white-space:nowrap">'
                f'Fluxo normal — aguardando D{last_d+1}</span>')
    if cat == "atrasado":
        next_d = last_d + 1
        ago = days_ago_str(last_d_ts)
        return (f'<span style="background:#2b0d0d;border:1px solid #E0525233;color:#E05252;'
                f'padding:3px 10px;border-radius:6px;font-size:0.7rem;'
                f'font-family:\'IBM Plex Mono\',monospace;white-space:nowrap">'
                f'Atrasado — D{next_d} deveria ter sido enviado ({ago} atrás)</span>')
    # sem_disparos / sem_dados
    return ('<span style="background:rgba(136,136,136,0.08);border:1px solid rgba(136,136,136,0.2);'
            'color:#888;padding:3px 10px;border-radius:6px;font-size:0.7rem;'
            'font-family:\'IBM Plex Mono\',monospace;white-space:nowrap">Sem disparos W0001</span>')

def build_row(idx, lead):
    name = esc(lead["name"].title() if lead["name"].isupper() else lead["name"])
    phone = fmt_phone(lead.get("phone",""))
    cat, last_d, last_d_ts = lead["cat"], lead["last_d"], lead["last_d_ts"]
    return (f'<tr>'
            f'<td style="color:rgba(245,240,232,0.4);font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;width:36px">{idx:02d}</td>'
            f'<td style="font-weight:600;color:#F5F0E8;white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis">{name}</td>'
            f'<td style="font-family:\'IBM Plex Mono\',monospace;font-size:0.78rem;color:rgba(245,240,232,0.6);white-space:nowrap">{phone}</td>'
            f'<td style="white-space:nowrap">{d_badge(last_d)}</td>'
            f'<td style="font-family:\'IBM Plex Mono\',monospace;font-size:0.75rem;color:rgba(245,240,232,0.5);white-space:nowrap">'
            f'{fmt_ts(last_d_ts)}<br><span style="color:rgba(245,240,232,0.3)">{days_ago_str(last_d_ts)}</span></td>'
            f'<td>{status_badge(cat, last_d, last_d_ts)}</td>'
            f'{link_btns(lead["deal_id"], lead.get("conv_id"))}'
            f'</tr>\n')

# ── Section builder ────────────────────────────────────────────────────────────
THEAD = ('<thead style="background:#161616"><tr>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">#</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Nome</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Telefone</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Último Disparo</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Enviado</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Status</th>'
         '<th style="padding:12px 14px;text-align:left;font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#DC9F42;border-bottom:1px solid rgba(220,159,66,0.12);white-space:nowrap">Links</th>'
         '</tr></thead>')

def build_table(leads):
    rows = "".join(build_row(i+1, l) for i, l in enumerate(leads))
    return (f'<div style="overflow-x:auto;border-radius:10px;border:1px solid rgba(220,159,66,0.12)">'
            f'<table style="width:100%;border-collapse:collapse">{THEAD}<tbody>{rows}</tbody></table></div>')

SDR_BADGE_COLORS = {
    OWNER_LAYSLLA: ("#9B6FD418", "#9B6FD433", "#9B6FD4"),
    OWNER_DAVID:   ("#2A9D8F18", "#2A9D8F33", "#2A9D8F"),
    OWNER_RAFA:    ("#DC9F4218", "#DC9F4233", "#DC9F42"),
}

def build_section_by_sdr(leads, title, count, color, icon=""):
    """Section with SDR breakdown (for Responderam and Atrasado)."""
    by_sdr = {}
    for l in leads:
        oid = l.get("owner_id", 0)
        by_sdr.setdefault(oid, []).append(l)

    sdr_html = ""
    for oid in SDR_ORDER:
        grp = by_sdr.get(oid, [])
        if not grp:
            continue
        name = OWNER_NAMES.get(oid, f"SDR {oid}")
        bg, bd, col = SDR_BADGE_COLORS.get(oid, ("#DC9F4218","#DC9F4233","#DC9F42"))
        sdr_html += (f'<div style="margin-bottom:32px">'
                     f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">'
                     f'<span style="font-family:\'DM Serif Display\',serif;font-size:1.2rem;color:#F5F0E8">{name}</span>'
                     f'<span style="background:{bg};border:1px solid {bd};color:{col};padding:4px 12px;border-radius:6px;font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace">{len(grp)} leads</span>'
                     f'</div>{build_table(grp)}</div>')
    # Other SDRs
    other = []
    for oid, grp in by_sdr.items():
        if oid not in SDR_ORDER:
            other.extend(grp)
    if other:
        sdr_html += (f'<div style="margin-bottom:32px">'
                     f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">'
                     f'<span style="font-family:\'DM Serif Display\',serif;font-size:1.2rem;color:#F5F0E8">Outros SDRs</span>'
                     f'<span style="background:#DC9F4218;border:1px solid #DC9F4233;color:#DC9F42;padding:4px 12px;border-radius:6px;font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace">{len(other)} leads</span>'
                     f'</div>{build_table(other)}</div>')

    return (f'<div style="padding:56px 0;border-bottom:1px solid rgba(220,159,66,0.12)">'
            f'<div style="max-width:1200px;margin:0 auto;padding:0 40px">'
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:28px">'
            f'<div style="width:4px;height:32px;background:{color};border-radius:2px"></div>'
            f'<h2 style="font-family:\'DM Serif Display\',serif;font-size:1.5rem;font-weight:400;color:#F5F0E8">{title}</h2>'
            f'<span style="background:{color}18;border:1px solid {color}33;color:{color};padding:4px 14px;border-radius:6px;font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace">{count} leads</span>'
            f'</div>{sdr_html}</div></div>')

def build_section_flat(leads, title, count, color):
    """Flat section without SDR grouping."""
    if not leads:
        return ""
    return (f'<div style="padding:56px 0;border-bottom:1px solid rgba(220,159,66,0.12)">'
            f'<div style="max-width:1200px;margin:0 auto;padding:0 40px">'
            f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:28px">'
            f'<div style="width:4px;height:32px;background:{color};border-radius:2px"></div>'
            f'<h2 style="font-family:\'DM Serif Display\',serif;font-size:1.5rem;font-weight:400;color:#F5F0E8">{title}</h2>'
            f'<span style="background:{color}18;border:1px solid {color}33;color:{color};padding:4px 14px;border-radius:6px;font-size:0.7rem;font-family:\'IBM Plex Mono\',monospace">{count} leads</span>'
            f'</div>{build_table(leads)}</div></div>')

# ── HTML template ──────────────────────────────────────────────────────────────
def build_html(leads_responded, leads_atrasado, leads_d4, leads_normal, leads_sem, total):
    n_resp = len(leads_responded)
    n_atra = len(leads_atrasado)
    n_d4   = len(leads_d4)
    n_norm = len(leads_normal)
    n_sem  = len(leads_sem)

    stats = f"""<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:16px">
      <div style="background:#0e0e0e;border:1px solid rgba(220,159,66,0.12);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#DC9F42;line-height:1;margin-bottom:8px">{total}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">Total na etapa</div>
      </div>
      <div style="background:#0e0e0e;border:1px solid rgba(76,175,111,0.25);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#4CAF6F;line-height:1;margin-bottom:8px">{n_resp}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">Responderam</div>
      </div>
      <div style="background:#0e0e0e;border:1px solid rgba(224,82,82,0.25);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#E05252;line-height:1;margin-bottom:8px">{n_atra}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">Fluxo atrasado</div>
      </div>
      <div style="background:#0e0e0e;border:1px solid rgba(155,111,212,0.25);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#9B6FD4;line-height:1;margin-bottom:8px">{n_d4}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">D4 — mover</div>
      </div>
      <div style="background:#0e0e0e;border:1px solid rgba(220,159,66,0.25);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#DC9F42;line-height:1;margin-bottom:8px">{n_norm}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">Fluxo normal</div>
      </div>
      <div style="background:#0e0e0e;border:1px solid rgba(85,85,85,0.4);border-radius:12px;padding:24px 20px">
        <div style="font-family:'DM Serif Display',serif;font-size:2.4rem;color:#888;line-height:1;margin-bottom:8px">{n_sem}</div>
        <div style="font-size:0.75rem;color:rgba(245,240,232,0.5);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:0.05em">Sem dados</div>
      </div>
    </div>"""

    sections = ""
    if leads_responded:
        sections += build_section_by_sdr(leads_responded,
            "Ação Imediata — Responderam (mover para Conectado)", n_resp, "#4CAF6F")
    if leads_atrasado:
        sections += build_section_by_sdr(leads_atrasado,
            "Fluxo Atrasado — W0001 não enviou próximo D", n_atra, "#E05252")
    if leads_d4:
        sections += build_section_flat(leads_d4,
            "D4 Concluído — Mover para Nutrição (stage 487)", n_d4, "#9B6FD4")
    if leads_normal:
        sections += build_section_flat(leads_normal,
            "Fluxo Normal — Aguardando próximo D dentro do SLA", n_norm, "#DC9F42")
    if leads_sem:
        sections += build_section_flat(leads_sem,
            "Sem Dados / Sem Disparos W0001", n_sem, "#888")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auditoria — Tentativa de Conectar · OMNI Brasil</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#050505;color:#F5F0E8;font-family:'Inter',sans-serif;line-height:1.65}}
  tr{{background:#0e0e0e;transition:background 0.15s;border-bottom:1px solid rgba(220,159,66,0.06)}}
  tr:hover{{background:rgba(220,159,66,0.06)}}
  td{{padding:12px 14px;vertical-align:top;font-size:0.875rem}}
  tr:last-child{{border-bottom:none}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}
</style>
</head>
<body>

<div style="padding:72px 0 48px;border-bottom:1px solid rgba(220,159,66,0.12)">
  <div style="max-width:1200px;margin:0 auto;padding:0 40px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
      <div style="width:6px;height:6px;background:#DC9F42;border-radius:50%"></div>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.6rem;font-weight:500;letter-spacing:0.1em;text-transform:uppercase;color:#DC9F42">SDR-Novo · Auditoria de Fluxo · OMNI Brasil</span>
    </div>
    <h1 style="font-family:'DM Serif Display',serif;font-size:clamp(2rem,3.5vw,3rem);font-weight:400;letter-spacing:-0.02em;color:#F5F0E8;line-height:1.1">Auditoria — Tentativa de Conectar</h1>
    <p style="font-size:0.95rem;color:rgba(245,240,232,0.6);margin-top:10px">Diagnóstico completo do fluxo D1→D4 por lead. Identifica quem respondeu, quem está atrasado e quem deve ser movido de etapa.</p>
    <div style="display:inline-flex;align-items:center;gap:8px;margin-top:16px;padding:8px 16px;background:rgba(76,175,111,0.08);border:1px solid rgba(76,175,111,0.20);border-radius:8px">
      <div style="width:7px;height:7px;background:#4CAF6F;border-radius:50%;animation:pulse 2s ease-in-out infinite"></div>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;letter-spacing:0.06em;text-transform:uppercase;color:rgba(76,175,111,0.7)">Auditado em</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.78rem;font-weight:500;color:#4CAF6F">{AUDIT_TIMESTAMP}</span>
    </div>
    <div style="display:inline-flex;align-items:center;gap:8px;margin-top:8px;margin-left:8px;padding:8px 16px;background:rgba(155,111,212,0.08);border:1px solid rgba(155,111,212,0.20);border-radius:8px">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;letter-spacing:0.06em;text-transform:uppercase;color:rgba(155,111,212,0.7)">Fix W0001</span>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:0.78rem;font-weight:500;color:#9B6FD4">Fase A + A.2 aplicados · W0001-R criado</span>
    </div>
  </div>
</div>

<div style="padding:48px 0;border-bottom:1px solid rgba(220,159,66,0.12)">
  <div style="max-width:1200px;margin:0 auto;padding:0 40px">
    {stats}
  </div>
</div>

{sections}

</body>
</html>"""

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    deals = fetch_deals_421()
    total = len(deals)

    leads_responded = []
    leads_atrasado  = []
    leads_d4        = []
    leads_normal    = []
    leads_sem       = []

    print(f"\nProcessando {total} deals...")
    for i, deal in enumerate(deals):
        did   = deal.get("id")
        title = deal.get("title", "?")
        person = deal.get("person_id") or {}
        pname  = person.get("name", title)
        phones = person.get("phone") or []
        phone_raw = next((p.get("value","") for p in phones if p.get("value")), "")
        owner   = deal.get("user_id") or {}
        owner_id = owner.get("id", 0)

        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{total}...", flush=True)

        # Pipedrive activities
        activities = fetch_activities(did)
        time.sleep(0.1)

        # Chatwoot
        contact_id = search_cw_contact(phone_raw)
        time.sleep(0.1)
        conv_id = None
        last_incoming_ts = None
        if contact_id:
            convs = fetch_cw_contact_convs(contact_id)
            time.sleep(0.1)
            conv_id, last_incoming_ts = get_best_conv(convs)

        cat, last_d, last_d_ts = classify(activities, last_incoming_ts)

        lead = {
            "deal_id":  did,
            "name":     pname or title,
            "phone":    phone_raw,
            "owner_id": owner_id,
            "conv_id":  conv_id,
            "cat":      cat,
            "last_d":   last_d,
            "last_d_ts": last_d_ts,
        }

        if cat == "responded":
            leads_responded.append(lead)
        elif cat == "atrasado":
            leads_atrasado.append(lead)
        elif cat == "d4_move":
            leads_d4.append(lead)
        elif cat == "normal":
            leads_normal.append(lead)
        else:
            leads_sem.append(lead)

    print(f"\nResultado:")
    print(f"  Responderam: {len(leads_responded)}")
    print(f"  Atrasados:   {len(leads_atrasado)}")
    print(f"  D4 mover:    {len(leads_d4)}")
    print(f"  Normal:      {len(leads_normal)}")
    print(f"  Sem dados:   {len(leads_sem)}")
    print(f"  Total:       {total}")

    html = build_html(leads_responded, leads_atrasado, leads_d4, leads_normal, leads_sem, total)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"\nHTML salvo: {OUT_PATH}")
    print(f"Auditado em: {AUDIT_TIMESTAMP}")

if __name__ == "__main__":
    main()
