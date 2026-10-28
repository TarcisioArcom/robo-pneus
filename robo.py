#!/usr/bin/env python3
"""
Robô de pesquisa de preços de pneus 185/65 R15 (somente novos, sem remold).

Fontes:
  1. Google Shopping via SerpApi   -> segredo SERPAPI_KEY (principal; agrega dezenas de lojas)
  2. Mercado Livre (API oficial)   -> segredo ML_ACCESS_TOKEN (opcional)
  3. Lojas VTEX listadas em config.json (API pública de catálogo, opcional)

Saídas:
  data/historico.csv          histórico de todas as ofertas válidas (1 linha por oferta/dia)
  data/AAAA-MM-DD.json        ofertas do dia
  docs/index.html             relatório comparativo (publicado no GitHub Pages)
  E-mail                      comparativo do dia -> EMAIL_USUARIO, EMAIL_SENHA_APP, EMAIL_DESTINO
  Telegram (opcional)         resumo com as 5 melhores ofertas -> TELEGRAM_TOKEN e TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import smtplib
import ssl
import sys
import unicodedata
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from statistics import mean, median
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

RAIZ = Path(__file__).parent
CONFIG = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo("America/Sao_Paulo")
HOJE = datetime.now(TZ).date().isoformat()
HEADERS = {"User-Agent": "Mozilla/5.0 (robo-pneus; +https://github.com)"}
TIMEOUT = 30

MARCAS = [
    "Pirelli", "Michelin", "Goodyear", "Bridgestone", "Continental", "Firestone",
    "Dunlop", "Yokohama", "Hankook", "Kumho", "Westlake", "Goodride", "Xbri",
    "Aptany", "Linglong", "Sailun", "Durable", "Barum", "General", "BFGoodrich",
    "Toyo", "Nexen", "Giti", "GT Radial", "Triangle", "Roadstone", "Ceat",
    "Wanli", "Delinte", "Cooper", "Maxxis", "Kelly", "Formula", "Itaro",
    "Landsail", "Rapid", "Habilead", "Kapsen", "Sunfull", "Zeetex", "Ovation",
    "Comforser", "Three-A", "Aeolus", "Double Coin", "Speedmax", "Atlas", "Hifly",
    "Momo", "Marshal", "Prinx", "Arisun", "Lassa", "Infinity", "Doublestar",
]


# --------------------------------------------------------------------------- utilidades
def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", txt.lower()).strip()


RE_MEDIDA = re.compile(r"185\s*[/\-\s]\s*65\s*[/\-\s]?\s*(?:z?r|aro)?\s*[/\-\s]?\s*15\b", re.I)
RE_KIT = re.compile(r"\b(?:kit|jogo|combo)\s*(?:c/|com|de)?\s*(\d)\b|\b(\d)\s*(?:pneus|unidades|un\.?)\b", re.I)
PROIBIDAS = [re.compile(rf"\b{re.escape(normalizar(p))}\b") for p in CONFIG["palavras_proibidas"]]


def oferta_valida(titulo: str) -> bool:
    t = normalizar(titulo)
    if not RE_MEDIDA.search(t):
        return False
    return not any(p.search(t) for p in PROIBIDAS)


def quantidade(titulo: str) -> int:
    m = RE_KIT.search(titulo)
    if not m:
        return 1
    q = int(m.group(1) or m.group(2))
    return q if 1 <= q <= 8 else 1


def marca(titulo: str) -> str:
    t = normalizar(titulo)
    for m in MARCAS:
        if re.search(rf"\b{re.escape(normalizar(m))}\b", t):
            return m
    return "Outra"


def montar(titulo, loja, preco_total, link, fonte):
    try:
        preco_total = float(preco_total)
    except (TypeError, ValueError):
        return None
    if not titulo or preco_total <= 0 or not oferta_valida(titulo):
        return None
    qtd = quantidade(titulo)
    unit = round(preco_total / qtd, 2)
    if not (CONFIG["preco_minimo_unitario"] <= unit <= CONFIG["preco_maximo_unitario"]):
        return None
    return {
        "data": HOJE,
        "fonte": fonte,
        "loja": (loja or "Desconhecida").strip(),
        "marca": marca(titulo),
        "titulo": titulo.strip(),
        "quantidade": qtd,
        "preco_total": round(preco_total, 2),
        "preco_unitario": unit,
        "link": link or "",
    }


# --------------------------------------------------------------------------- fontes
def fonte_google_shopping() -> list[dict]:
    chave = os.getenv("SERPAPI_KEY")
    if not chave:
        print("[google] SERPAPI_KEY ausente, fonte ignorada")
        return []
    params = {
        "engine": "google_shopping", "q": CONFIG["consulta"], "gl": "br", "hl": "pt-br",
        "google_domain": "google.com.br", "location": "Brazil", "num": 100, "api_key": chave,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    r.raise_for_status()
    dados = r.json()
    itens = dados.get("shopping_results", []) + dados.get("inline_shopping_results", [])
    saida = []
    for it in itens:
        o = montar(it.get("title"), it.get("source"), it.get("extracted_price"),
                   it.get("product_link") or it.get("link"), "Google Shopping")
        if o:
            saida.append(o)
    print(f"[google] {len(itens)} resultados, {len(saida)} válidos")
    return saida


def fonte_mercado_livre() -> list[dict]:
    token = os.getenv("ML_ACCESS_TOKEN")
    if not token:
        print("[mercado livre] ML_ACCESS_TOKEN ausente, fonte ignorada")
        return []
    saida, total = [], 0
    for offset in (0, 50, 100):
        r = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": CONFIG["consulta"], "condition": "new", "offset": offset, "limit": 50},
            headers={**HEADERS, "Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
        )
        if r.status_code != 200:
            print(f"[mercado livre] HTTP {r.status_code}: {r.text[:200]}")
            break
        res = r.json().get("results", [])
        total += len(res)
        for it in res:
            if it.get("condition") != "new":
                continue
            loja = (it.get("seller") or {}).get("nickname") or "Mercado Livre"
            o = montar(it.get("title"), f"Mercado Livre ({loja})", it.get("price"),
                       it.get("permalink"), "Mercado Livre")
            if o:
                saida.append(o)
    print(f"[mercado livre] {total} resultados, {len(saida)} válidos")
    return saida


def fonte_vtex() -> list[dict]:
    saida = []
    termo = quote(CONFIG["medida"])
    for loja in CONFIG.get("lojas_vtex", []):
        nome, dominio = loja["nome"], loja["dominio"].rstrip("/")
        url = f"https://{dominio}/api/catalog_system/pub/products/search?ft={termo}&_from=0&_to=49"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            produtos = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[vtex:{nome}] falhou: {e}")
            continue
        n = 0
        for p in produtos:
            for item in p.get("items", []):
                for seller in item.get("sellers", []):
                    of = seller.get("commertialOffer", {})
                    if of.get("AvailableQuantity", 0) <= 0:
                        continue
                    o = montar(p.get("productName"), nome, of.get("Price"), p.get("link"), "VTEX")
                    if o:
                        saida.append(o)
                        n += 1
        print(f"[vtex:{nome}] {len(produtos)} produtos, {n} válidos")
    return saida


# --------------------------------------------------------------------------- persistência
CAMPOS = ["data", "fonte", "loja", "marca", "titulo", "quantidade", "preco_total", "preco_unitario", "link"]


def deduplicar(ofertas: list[dict]) -> list[dict]:
    melhores: dict[tuple, dict] = {}
    for o in ofertas:
        chave = (normalizar(o["loja"]), normalizar(o["titulo"]))
        if chave not in melhores or o["preco_unitario"] < melhores[chave]["preco_unitario"]:
            melhores[chave] = o
    return sorted(melhores.values(), key=lambda x: x["preco_unitario"])


def salvar(ofertas: list[dict]) -> list[dict]:
    pasta = RAIZ / "data"
    pasta.mkdir(exist_ok=True)
    (pasta / f"{HOJE}.json").write_text(json.dumps(ofertas, ensure_ascii=False, indent=2), encoding="utf-8")
    arq = pasta / "historico.csv"
    linhas = []
    if arq.exists():
        with arq.open(encoding="utf-8") as f:
            linhas = [l for l in csv.DictReader(f) if l["data"] != HOJE]  # reexecução no mesmo dia substitui
    linhas += ofertas
    with arq.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(linhas)
    return linhas


# --------------------------------------------------------------------------- relatório
def brl(v: float) -> str:
    txt = f"R$ {abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{txt}" if v < 0 else txt


def serie_diaria(historico: list[dict]) -> list[tuple[str, float, float]]:
    por_dia: dict[str, list[float]] = {}
    for l in historico:
        por_dia.setdefault(l["data"], []).append(float(l["preco_unitario"]))
    return [(d, min(v), median(v)) for d, v in sorted(por_dia.items())][-30:]


def resumo_diario(historico: list[dict]) -> list[dict]:
    """Uma linha por dia de coleta: menor preço (com marca/loja/link), mediana, nº de ofertas e de lojas."""
    dias: dict[str, list[dict]] = {}
    for l in historico:
        dias.setdefault(l["data"], []).append(l)
    saida, anterior = [], None
    for d in sorted(dias):
        linhas = dias[d]
        precos = [float(l["preco_unitario"]) for l in linhas]
        melhor = min(linhas, key=lambda l: float(l["preco_unitario"]))
        r = {"data": d, "menor": min(precos), "mediana": median(precos), "media": mean(precos),
             "marca": melhor["marca"], "loja": melhor["loja"], "link": melhor["link"],
             "ofertas": len(linhas), "lojas": len({l["loja"] for l in linhas}),
             "variacao": None if anterior is None else min(precos) - anterior}
        anterior = r["menor"]
        saida.append(r)
    return saida


def data_br(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"


def grafico_svg(serie) -> str:
    if len(serie) < 2:
        return '<p class="vazio">O gráfico aparece a partir do segundo dia de coleta.</p>'
    W, H, P = 720, 220, 40
    vals = [v for _, a, b in serie for v in (a, b)]
    lo, hi = min(vals) * 0.95, max(vals) * 1.05
    x = lambda i: P + i * (W - 2 * P) / (len(serie) - 1)
    y = lambda v: H - P + 10 - (v - lo) / (hi - lo or 1) * (H - 2 * P)
    linha = lambda idx: " ".join(f"{x(i):.1f},{y(s[idx]):.1f}" for i, s in enumerate(serie))
    pts = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(s[1]):.1f}" r="3.5" class="pt"><title>{s[0]}: {brl(s[1])}</title></circle>'
        for i, s in enumerate(serie))
    rot = "".join(
        f'<text x="{x(i):.1f}" y="{H - 8}" class="eixo" text-anchor="middle">{s[0][8:]}/{s[0][5:7]}</text>'
        for i, s in enumerate(serie) if len(serie) <= 12 or i % 3 == 0)
    grade = "".join(
        f'<line x1="{P}" x2="{W - P}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grade"/>'
        f'<text x="{P - 6}" y="{y(v) + 4:.1f}" class="eixo" text-anchor="end">{int(v)}</text>'
        for v in [lo + (hi - lo) * k / 3 for k in range(4)])
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Menor preço e mediana por dia">'
            f'{grade}<polyline points="{linha(2)}" class="mediana"/>'
            f'<polyline points="{linha(1)}" class="minimo"/>{pts}{rot}</svg>'
            '<div class="legenda"><span class="l-min">Menor preço</span><span class="l-med">Mediana</span></div>')


def tabela_historico_html(historico: list[dict]) -> str:
    e = html.escape
    dias = resumo_diario(historico)
    if not dias:
        return ""
    rec = min(dias, key=lambda r: r["menor"])
    linhas = ""
    for r in reversed(dias[-60:]):
        v = r["variacao"]
        var = "" if v is None else f'<span class="{"sobe" if v > 0 else "desce" if v < 0 else ""}">{"+" if v > 0 else ""}{brl(v)}</span>'
        linhas += (f'<tr><td>{data_br(r["data"])}</td><td class="num"><b>{brl(r["menor"])}</b></td><td class="num">{var}</td>'
                   f'<td><a href="{e(r["link"])}" target="_blank" rel="noopener">{e(r["marca"])}, {e(r["loja"])}</a></td>'
                   f'<td class="num">{brl(r["mediana"])}</td><td class="num">{r["ofertas"]}</td></tr>')
    return (f'<section class="card"><h2>Histórico das pesquisas</h2>'
            f'<p class="recorde">Menor preço já registrado: <b>{brl(rec["menor"])}</b> em {data_br(rec["data"])} '
            f'({e(rec["marca"])}, {e(rec["loja"])}). {len(dias)} pesquisas desde {data_br(dias[0]["data"])}.</p>'
            '<div class="rolagem"><table><thead><tr><th>Data</th><th class="num">Menor preço</th><th class="num">Variação</th>'
            '<th>Onde</th><th class="num">Mediana</th><th class="num">Ofertas</th></tr></thead>'
            f'<tbody>{linhas}</tbody></table></div></section>')


def gerar_html(ofertas: list[dict], historico: list[dict]) -> str:
    e = html.escape
    serie = serie_diaria(historico)
    agora = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")
    if not ofertas:
        corpo = '<section class="card"><p class="vazio">Nenhuma oferta válida hoje. Confira os logs do GitHub Actions e se os segredos SERPAPI_KEY / ML_ACCESS_TOKEN estão configurados.</p></section>' + tabela_historico_html(historico)
    else:
        top = ofertas[0]
        precos = [o["preco_unitario"] for o in ofertas]
        ontem = serie[-2][1] if len(serie) >= 2 and serie[-1][0] == HOJE else None
        if ontem:
            dif = top["preco_unitario"] - ontem
            cls = "sobe" if dif > 0 else "desce" if dif < 0 else ""
            var = f'<span class="var {cls}">{"+" if dif > 0 else ""}{brl(dif)} vs. coleta anterior</span>'
        else:
            var = ""
        por_marca: dict[str, dict] = {}
        for o in ofertas:
            if o["marca"] not in por_marca or o["preco_unitario"] < por_marca[o["marca"]]["preco_unitario"]:
                por_marca[o["marca"]] = o
        linhas_marca = "".join(
            f'<tr><td>{e(m)}</td><td class="num">{brl(o["preco_unitario"])}</td><td>{e(o["loja"])}</td>'
            f'<td><a href="{e(o["link"])}" target="_blank" rel="noopener">Ver oferta</a></td></tr>'
            for m, o in sorted(por_marca.items(), key=lambda kv: kv[1]["preco_unitario"]))
        linhas_top = "".join(
            f'<tr><td class="num">{i}</td><td>{e(o["titulo"])}{" <small>(kit c/ " + str(o["quantidade"]) + ")</small>" if o["quantidade"] > 1 else ""}</td>'
            f'<td>{e(o["marca"])}</td><td>{e(o["loja"])}</td><td class="num">{brl(o["preco_unitario"])}</td>'
            f'<td><a href="{e(o["link"])}" target="_blank" rel="noopener">Abrir</a></td></tr>'
            for i, o in enumerate(ofertas[: CONFIG["max_ofertas_relatorio"]], 1))
        corpo = f"""
<section class="destaque">
  <p class="rotulo">Menor preço por pneu hoje</p>
  <p class="preco">{brl(top['preco_unitario'])}</p>
  <p class="detalhe">{e(top['marca'])} na {e(top['loja'])} {var}</p>
  <a class="botao" href="{e(top['link'])}" target="_blank" rel="noopener">Abrir a oferta</a>
</section>
<section class="kpis">
  <div><strong>{brl(median(precos))}</strong><span>Mediana</span></div>
  <div><strong>{brl(mean(precos))}</strong><span>Média</span></div>
  <div><strong>{len(ofertas)}</strong><span>Ofertas válidas</span></div>
  <div><strong>{len({o['loja'] for o in ofertas})}</strong><span>Lojas</span></div>
</section>
<section class="card"><h2>Evolução (últimos 30 dias de coleta)</h2>{grafico_svg(serie)}</section>
{tabela_historico_html(historico)}
<section class="card"><h2>Mais barato por marca</h2><div class="rolagem"><table>
<thead><tr><th>Marca</th><th class="num">Preço/un.</th><th>Loja</th><th></th></tr></thead><tbody>{linhas_marca}</tbody></table></div></section>
<section class="card"><h2>Ranking das ofertas</h2><div class="rolagem"><table>
<thead><tr><th class="num">#</th><th>Produto</th><th>Marca</th><th>Loja</th><th class="num">Preço/un.</th><th></th></tr></thead><tbody>{linhas_top}</tbody></table></div></section>"""
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Comparativo de pneus {e(CONFIG['medida'])} — Arcom</title>
<style>
:root{{--brand:#009540;--brand-dark:#007a34;--brand-light:#e6f5ec;--bg:#f7f8fa;--card:#fff;--border:#e5e7eb;
--text:#111827;--muted:#6b7280;--danger:#b91c1c;--ok:#047857;--radius:10px;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1114;--card:#17191d;--border:#262a30;--text:#e5e7eb;--muted:#9ca3af;
--brand-light:#0f2b1c;--danger:#f87171;--ok:#34d399;color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;line-height:1.5}}
header{{background:var(--brand);color:#fff;padding:20px max(16px,calc((100% - 1000px)/2))}}
header h1{{margin:0;font-size:1.35rem}} header p{{margin:2px 0 0;opacity:.85;font-size:.9rem}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 40px;display:grid;gap:16px}}
.destaque{{background:var(--brand-light);border:1px solid var(--brand);border-radius:var(--radius);padding:24px}}
.rotulo{{margin:0;color:var(--muted)}} .preco{{margin:4px 0;font-size:clamp(2.2rem,7vw,3.4rem);font-weight:700;color:var(--brand);font-variant-numeric:tabular-nums}}
.detalhe{{margin:0 0 14px}} .var{{margin-left:8px;font-size:.9rem}} .sobe{{color:var(--danger)}} .desce{{color:var(--ok)}}
.botao{{display:inline-block;background:var(--brand);color:#fff;padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600}}
.botao:hover{{background:var(--brand-dark)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.kpis div{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px}}
.kpis strong{{display:block;font-size:1.25rem;font-variant-numeric:tabular-nums}} .kpis span{{color:var(--muted);font-size:.85rem}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px}}
h2{{margin:0 0 12px;font-size:1.05rem}}
.rolagem{{overflow-x:auto}} table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}} .num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.recorde{{margin:0 0 12px;color:var(--muted)}} .recorde b{{color:var(--brand)}}
a{{color:var(--brand)}} small{{color:var(--muted)}} .vazio{{color:var(--muted);margin:0}}
svg{{width:100%;height:auto}} .grade{{stroke:var(--border)}} .eixo{{fill:var(--muted);font-size:11px}}
.minimo{{fill:none;stroke:var(--brand);stroke-width:2.5}} .mediana{{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}}
.pt{{fill:var(--brand)}} .legenda{{display:flex;gap:16px;font-size:.85rem;color:var(--muted)}}
.l-min::before,.l-med::before{{content:"";display:inline-block;width:14px;height:3px;margin-right:6px;vertical-align:middle;background:var(--brand)}}
.l-med::before{{background:var(--muted)}}
footer{{text-align:center;color:var(--muted);font-size:.8rem;padding-bottom:24px}}
</style></head><body>
<header><h1>Pneus {e(CONFIG['medida'])} novos</h1><p>Comparativo diário de preços, sem remold. Atualizado em {agora}.</p></header>
<main>{corpo}</main>
<footer>Arcom. Preços coletados automaticamente; confirme frete e disponibilidade na loja. <a href="historico.csv">Baixar histórico (CSV)</a></footer>
</body></html>"""


# --------------------------------------------------------------------------- notificação
def notificar_telegram(ofertas: list[dict]) -> None:
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    if ofertas:
        linhas = [f"🛞 Pneus {CONFIG['medida']} (novos) — {datetime.now(TZ):%d/%m}"]
        for i, o in enumerate(ofertas[:5], 1):
            linhas.append(f"{i}. {brl(o['preco_unitario'])} · {o['marca']} · {o['loja']}\n{o['link']}")
        site = os.getenv("URL_RELATORIO")
        if site:
            linhas.append(f"\nRelatório completo: {site}")
        texto = "\n".join(linhas)
    else:
        texto = "⚠️ Robô de pneus: nenhuma oferta válida hoje. Verifique o GitHub Actions."
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat, "text": texto, "disable_web_page_preview": True}, timeout=TIMEOUT)


# --------------------------------------------------------------------------- e-mail
SMTP_PADRAO = {  # servidor escolhido pelo domínio do remetente; pode ser trocado por SMTP_HOST/SMTP_PORTA
    "yahoo.com": ("smtp.mail.yahoo.com", 465), "yahoo.com.br": ("smtp.mail.yahoo.com", 465),
    "gmail.com": ("smtp.gmail.com", 465), "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
}


def html_email(ofertas: list[dict], historico: list[dict]) -> str:
    """HTML com estilos inline e tabelas, que é o que os leitores de e-mail (Yahoo, Gmail, Outlook) exibem bem."""
    e = html.escape
    verde, verde_claro, cinza, borda, texto = "#009540", "#e6f5ec", "#6b7280", "#e5e7eb", "#111827"
    fonte = "font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    site = os.getenv("URL_RELATORIO", "")
    hoje_br = datetime.now(TZ).strftime("%d/%m/%Y")
    td = f"padding:8px 10px;border-bottom:1px solid {borda};{fonte}font-size:14px;color:{texto};"
    tdn = td + "text-align:right;white-space:nowrap;"
    th = f"padding:8px 10px;border-bottom:2px solid {borda};{fonte}font-size:12px;color:{cinza};text-align:left;"

    # ---- histórico das pesquisas (barras feitas com tabela, que funcionam em qualquer leitor de e-mail)
    dias = resumo_diario(historico)
    bloco_hist = ""
    if dias:
        n = CONFIG.get("dias_historico_email", 20)
        rec = min(dias, key=lambda r: r["menor"])
        ult = dias[-n:]
        lo, hi = min(r["menor"] for r in ult), max(r["menor"] for r in ult)
        linhas_h = ""
        for r in reversed(ult):
            larg = 30 + int(70 * (r["menor"] - lo) / (hi - lo)) if hi > lo else 60
            v = r["variacao"]
            cor_v = "#b91c1c" if v and v > 0 else "#047857" if v and v < 0 else cinza
            txt_v = "" if v is None else f'{"+" if v > 0 else ""}{brl(v)}'
            destaque = "font-weight:700;" if r["data"] == rec["data"] else ""
            linhas_h += (
                f'<tr><td style="{td}white-space:nowrap">{data_br(r["data"])[:5]}</td>'
                f'<td style="{td}" width="40%"><table cellpadding="0" cellspacing="0" width="{larg}%"><tr>'
                f'<td style="background:{verde};height:10px;font-size:0;line-height:0;border-radius:3px">&nbsp;</td></tr></table></td>'
                f'<td style="{tdn}{destaque}">{brl(r["menor"])}</td>'
                f'<td style="{tdn}color:{cor_v};font-size:12px">{txt_v}</td>'
                f'<td style="{td}font-size:12px;color:{cinza}">{e(r["marca"])}<br>{e(r["loja"])}</td></tr>')
        bloco_hist = f"""
<h2 style="{fonte}font-size:16px;color:{texto};margin:24px 0 4px">Histórico das pesquisas</h2>
<p style="{fonte}font-size:13px;color:{cinza};margin:0 0 8px">Menor preço já registrado: <b style="color:{verde}">{brl(rec['menor'])}</b>
em {data_br(rec['data'])} ({e(rec['marca'])}, {e(rec['loja'])}). {len(dias)} pesquisas desde {data_br(dias[0]['data'])};
abaixo, as últimas {len(ult)}. O histórico completo segue em anexo.</p>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr><th style="{th}">Data</th><th style="{th}">Menor preço do dia</th><th style="{th}text-align:right"></th>
<th style="{th}text-align:right">Variação</th><th style="{th}">Onde</th></tr>{linhas_h}</table>"""

    if not ofertas:
        miolo = (f'<p style="{fonte}font-size:15px;color:{texto}">Nenhuma oferta válida foi encontrada hoje. '
                 'Confira o log na aba Actions do GitHub.</p>') + bloco_hist
    else:
        top, precos = ofertas[0], [o["preco_unitario"] for o in ofertas]
        serie = serie_diaria(historico)
        var = ""
        if len(serie) >= 2 and serie[-1][0] == HOJE:
            dif = top["preco_unitario"] - serie[-2][1]
            cor = "#b91c1c" if dif > 0 else "#047857" if dif < 0 else cinza
            var = (f'<div style="{fonte}font-size:13px;color:{cor};margin-top:4px">'
                   f'{"+" if dif > 0 else ""}{brl(dif)} em relação à coleta anterior</div>')
        por_marca: dict[str, dict] = {}
        for o in ofertas:
            if o["marca"] not in por_marca or o["preco_unitario"] < por_marca[o["marca"]]["preco_unitario"]:
                por_marca[o["marca"]] = o
        marcas = "".join(
            f'<tr><td style="{td}">{e(m)}</td><td style="{tdn}"><b>{brl(o["preco_unitario"])}</b></td>'
            f'<td style="{td}"><a href="{e(o["link"])}" style="color:{verde}">{e(o["loja"])}</a></td></tr>'
            for m, o in sorted(por_marca.items(), key=lambda kv: kv[1]["preco_unitario"]))
        ranking = "".join(
            f'<tr><td style="{tdn}">{i}</td><td style="{td}"><a href="{e(o["link"])}" style="color:{texto};text-decoration:none">'
            f'{e(o["titulo"])}</a>{" <span style=color:" + cinza + ">(kit c/ " + str(o["quantidade"]) + ")</span>" if o["quantidade"] > 1 else ""}'
            f'<br><span style="color:{cinza};font-size:12px">{e(o["loja"])}</span></td>'
            f'<td style="{tdn}"><b>{brl(o["preco_unitario"])}</b></td></tr>'
            for i, o in enumerate(ofertas[:15], 1))
        kpi = lambda v, r: (f'<td style="padding:10px;border:1px solid {borda};{fonte}" width="25%">'
                            f'<div style="font-size:16px;font-weight:700;color:{texto}">{v}</div>'
                            f'<div style="font-size:12px;color:{cinza}">{r}</div></td>')
        miolo = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:{verde_claro};border:1px solid {verde};border-radius:8px">
<tr><td style="padding:20px;{fonte}">
  <div style="font-size:13px;color:{cinza}">Menor preço por pneu hoje</div>
  <div style="font-size:34px;font-weight:700;color:{verde};margin:2px 0">{brl(top['preco_unitario'])}</div>
  <div style="font-size:14px;color:{texto}">{e(top['marca'])} na {e(top['loja'])}</div>{var}
  <div style="margin-top:14px"><a href="{e(top['link'])}" style="background:{verde};color:#ffffff;padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600;{fonte}font-size:14px">Abrir a oferta</a></div>
</td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;border-collapse:collapse"><tr>
{kpi(brl(median(precos)), "Mediana")}{kpi(brl(mean(precos)), "Média")}{kpi(len(ofertas), "Ofertas válidas")}{kpi(len({o['loja'] for o in ofertas}), "Lojas")}
</tr></table>
{bloco_hist}
<h2 style="{fonte}font-size:16px;color:{texto};margin:24px 0 8px">Mais barato por marca</h2>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr><th style="{th}">Marca</th><th style="{th}text-align:right">Preço/un.</th><th style="{th}">Loja</th></tr>{marcas}</table>
<h2 style="{fonte}font-size:16px;color:{texto};margin:24px 0 8px">As 15 melhores ofertas</h2>
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr><th style="{th}text-align:right">#</th><th style="{th}">Produto</th><th style="{th}text-align:right">Preço/un.</th></tr>{ranking}</table>"""
    link_site = (f'<p style="{fonte}font-size:14px;margin:20px 0 0"><a href="{e(site)}" style="color:{verde}">'
                 'Ver o relatório completo com gráfico e histórico</a></p>') if site else ""
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7f8fa">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f8fa"><tr><td align="center" style="padding:16px">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border:1px solid {borda};border-radius:10px">
<tr><td style="background:{verde};padding:18px 22px;border-radius:10px 10px 0 0;{fonte}">
  <div style="font-size:19px;font-weight:700;color:#ffffff">Pneus {e(CONFIG['medida'])} novos</div>
  <div style="font-size:13px;color:#e6f5ec">Comparativo de preços de {hoje_br}, sem remold</div>
</td></tr>
<tr><td style="padding:22px">{miolo}{link_site}
<p style="{fonte}font-size:12px;color:{cinza};margin:20px 0 0">Arcom. Preços coletados automaticamente; confirme frete e disponibilidade na loja. A planilha do dia segue em anexo.</p>
</td></tr></table></td></tr></table></body></html>"""


def num_br(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def csv_excel(cabecalho: list, linhas: list[list]) -> bytes:
    """CSV com ponto e vírgula e BOM: abre direto no Excel em português com acentos corretos."""
    limpa = lambda c: str(c).replace(";", ",").replace("\n", " ")
    return ("\ufeff" + "\n".join(";".join(limpa(c) for c in l) for l in [cabecalho, *linhas])).encode("utf-8")


def enviar_email(ofertas: list[dict], historico: list[dict]) -> None:
    usuario, senha = os.getenv("EMAIL_USUARIO"), os.getenv("EMAIL_SENHA_APP")
    destinos = [d.strip() for d in (os.getenv("EMAIL_DESTINO") or usuario or "").split(",") if d.strip()]
    if not (usuario and senha and destinos):
        print("[email] EMAIL_USUARIO / EMAIL_SENHA_APP ausentes, e-mail não enviado")
        return
    dominio = usuario.split("@")[-1].lower()
    host, porta = SMTP_PADRAO.get(dominio, ("smtp.mail.yahoo.com", 465))
    host, porta = os.getenv("SMTP_HOST") or host, int(os.getenv("SMTP_PORTA") or porta)

    hoje_br = datetime.now(TZ).strftime("%d/%m")
    if ofertas:
        assunto = f"Pneus {CONFIG['medida']} em {hoje_br}: menor preço {brl(ofertas[0]['preco_unitario'])} ({ofertas[0]['marca']})"
        texto = "\n".join(f"{i}. {brl(o['preco_unitario'])} - {o['marca']} - {o['loja']}\n   {o['link']}"
                          for i, o in enumerate(ofertas[:15], 1))
    else:
        assunto = f"Pneus {CONFIG['medida']} em {hoje_br}: nenhuma oferta encontrada"
        texto = "Nenhuma oferta válida hoje. Confira o log na aba Actions do GitHub."

    dias = resumo_diario(historico)
    if dias:
        texto += "\n\nHistórico (menor preço por dia):\n" + "\n".join(
            f"{data_br(r['data'])}: {brl(r['menor'])} - {r['marca']} - {r['loja']}" for r in reversed(dias[-20:]))

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, f"Robô Pneus <{usuario}>", ", ".join(destinos)
    msg.set_content(texto)  # versão texto puro, para leitores sem HTML
    msg.add_alternative(html_email(ofertas, historico), subtype="html")
    if ofertas:
        buf = [";".join(["Marca", "Loja", "Produto", "Qtd", "Preço total", "Preço unitário", "Link"])]
        for o in ofertas:
            buf.append(";".join([o["marca"], o["loja"], o["titulo"].replace(";", ","), str(o["quantidade"]),
                                 f"{o['preco_total']:.2f}".replace(".", ","),
                                 f"{o['preco_unitario']:.2f}".replace(".", ","), o["link"]]))
        # CSV com ponto e vírgula e BOM: abre direto no Excel em português com acentos corretos
        msg.add_attachment(("\ufeff" + "\n".join(buf)).encode("utf-8"), maintype="text", subtype="csv",
                           filename=f"pneus-185-65-r15-{HOJE}.csv")

    if dias:
        msg.add_attachment(csv_excel(
            ["Data", "Menor preço", "Variação", "Marca", "Loja", "Mediana", "Média", "Ofertas", "Lojas", "Link"],
            [[data_br(r["data"]), num_br(r["menor"]), "" if r["variacao"] is None else num_br(r["variacao"]),
              r["marca"], r["loja"], num_br(r["mediana"]), num_br(r["media"]), r["ofertas"], r["lojas"], r["link"]]
             for r in reversed(dias)]),
            maintype="text", subtype="csv", filename="historico-resumo-por-dia.csv")
        msg.add_attachment(csv_excel(
            ["Data", "Marca", "Loja", "Produto", "Qtd", "Preço total", "Preço unitário", "Fonte", "Link"],
            [[data_br(l["data"]), l["marca"], l["loja"], l["titulo"], l["quantidade"], num_br(float(l["preco_total"])),
              num_br(float(l["preco_unitario"])), l["fonte"], l["link"]]
             for l in sorted(historico, key=lambda l: (l["data"], float(l["preco_unitario"])), reverse=True)]),
            maintype="text", subtype="csv", filename="historico-completo-ofertas.csv")

    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=ctx, timeout=TIMEOUT) as s:
            s.login(usuario, senha)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, porta, timeout=TIMEOUT) as s:
            s.starttls(context=ctx)
            s.login(usuario, senha)
            s.send_message(msg)
    print(f"[email] enviado para {', '.join(destinos)}")


# --------------------------------------------------------------------------- main
def main() -> int:
    ofertas = []
    for fonte in (fonte_google_shopping, fonte_mercado_livre, fonte_vtex):
        try:
            ofertas += fonte()
        except Exception as e:  # noqa: BLE001  (uma fonte falhar não derruba o robô)
            print(f"[erro] {fonte.__name__}: {e}")
    ofertas = deduplicar(ofertas)
    historico = salvar(ofertas)
    docs = RAIZ / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(gerar_html(ofertas, historico), encoding="utf-8")
    (docs / "historico.csv").write_text((RAIZ / "data" / "historico.csv").read_text(encoding="utf-8"), encoding="utf-8")
    try:
        enviar_email(ofertas, historico)
    except Exception as e:  # noqa: BLE001
        print(f"[email] falhou: {e}")
        return 2
    notificar_telegram(ofertas)
    print(f"Concluído: {len(ofertas)} ofertas válidas em {HOJE}")
    return 0 if ofertas else 1


if __name__ == "__main__":
    sys.exit(main())
