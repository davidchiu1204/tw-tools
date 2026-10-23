#!/usr/bin/env python3
"""台灣生活查詢站 - 資料抓取 + 靜態網站產生器。

只使用 Python 標準函式庫，不需要安裝任何套件。

用法:
    python scripts/build.py           # 抓最新資料並產生網站到 dist/
    python scripts/build.py --offline # 不抓資料，只用 data/ 快取重新產生網站
    python scripts/build.py --check   # 檢查資料是否過舊（給 GitHub Actions 用，過舊會回傳非 0）
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
STATIC = ROOT / "static"
TW = timezone(timedelta(hours=8))
NOW = datetime.now(TW)
UA = {"User-Agent": "Mozilla/5.0 (compatible; tw-tools-bot/1.0)"}

INVOICE_RSS = "https://invoice.etax.nat.gov.tw/invoice.xml"
CPC_XML = "https://vipmbr.cpc.com.tw/CPCSTN/ListPriceWebService.asmx/getCPCMainProdListPrice_XML"
RATE_API = "https://open.er-api.com/v6/latest/TWD"

FUELS = ["92無鉛汽油", "95無鉛汽油", "98無鉛汽油", "酒精汽油", "超級柴油"]

CURRENCIES = [
    ("USD", "美金", "美元", "🇺🇸"),
    ("JPY", "日圓", "日幣", "🇯🇵"),
    ("EUR", "歐元", "歐元", "🇪🇺"),
    ("CNY", "人民幣", "人民幣", "🇨🇳"),
    ("HKD", "港幣", "港元", "🇭🇰"),
    ("KRW", "韓元", "韓幣", "🇰🇷"),
    ("GBP", "英鎊", "英鎊", "🇬🇧"),
    ("AUD", "澳幣", "澳元", "🇦🇺"),
    ("SGD", "新加坡幣", "新幣", "🇸🇬"),
    ("THB", "泰銖", "泰幣", "🇹🇭"),
    ("VND", "越南盾", "越南幣", "🇻🇳"),
    ("CAD", "加拿大幣", "加幣", "🇨🇦"),
    ("MYR", "馬來西亞幣", "令吉", "🇲🇾"),
    ("PHP", "菲律賓披索", "菲幣", "🇵🇭"),
    ("IDR", "印尼盾", "印尼幣", "🇮🇩"),
    ("NZD", "紐西蘭幣", "紐幣", "🇳🇿"),
    ("CHF", "瑞士法郎", "瑞郎", "🇨🇭"),
]
CUR_NAME = {c[0]: c[1] for c in CURRENCIES}

FIRST_PRIZE_TIERS = [
    (8, "頭獎", 200000),
    (7, "二獎", 40000),
    (6, "三獎", 10000),
    (5, "四獎", 4000),
    (4, "五獎", 1000),
    (3, "六獎", 200),
]


# --------------------------------------------------------------------------- utils
def log(msg: str) -> None:
    print(msg, flush=True)


def _ssl_ctx():
    # Python 3.13+ 預設啟用 VERIFY_X509_STRICT，部分台灣政府網站憑證缺少 Subject Key Identifier 會被拒絕。
    # 這裡只關掉 strict 旗標，仍然完整驗證憑證鏈與主機名稱。
    import ssl
    ctx = ssl.create_default_context()
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


SSL_CTX = _ssl_ctx()


def fetch(url: str, tries: int = 3) -> bytes:
    import time
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last  # type: ignore[misc]


def load_json(name: str, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 讀取 {name} 失敗: {e}")
    return default


def save_json(name: str, obj) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def roc_to_date(s: str) -> date:
    s = s.strip()
    y, m, d = int(s[:-4]), int(s[-4:-2]), int(s[-2:])
    return date(y + 1911, m, d)


def add_months(y: int, m: int, n: int) -> tuple[int, int]:
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def fmt_date(d: date) -> str:
    return f"{d.year}/{d.month:02d}/{d.day:02d}"


def fmt_date_roc(d: date) -> str:
    return f"{d.year - 1911}年{d.month}月{d.day}日"


def fmt_num(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}"


def smart_rate(x: float) -> str:
    if x >= 100:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    if x >= 0.01:
        return f"{x:.5f}"
    return f"{x:.7f}"


# --------------------------------------------------------------------------- fetchers
def update_invoice() -> dict:
    store = load_json("invoice.json", {"periods": {}, "updated": None})
    try:
        root = ET.fromstring(fetch(INVOICE_RSS))
        n = 0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            m = re.search(r"(\d{2,3})年\s*(\d{1,2})\s*[~～-]\s*(\d{1,2})月", title)
            if not m:
                continue
            roc_y, m1, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            key = f"{roc_y}{m1:02d}"
            sp = re.search(r"特別獎[：:]\s*(\d{8})", desc)
            gp = re.search(r"特獎[：:]\s*(\d{8})", desc)
            fp = re.search(r"頭獎[：:]\s*([\d、,，\s]+)", desc)
            ex = re.search(r"增開六獎[：:]\s*([\d、,，\s]+)", desc)
            if not (sp and gp and fp):
                continue
            first = re.findall(r"\d{8}", fp.group(1))
            extra = re.findall(r"\d{3}", ex.group(1)) if ex else []
            store["periods"][key] = {
                "key": key,
                "roc_year": roc_y,
                "m1": m1,
                "m2": m2,
                "special": sp.group(1),
                "grand": gp.group(1),
                "first": first,
                "extra": extra,
                "source": link,
            }
            n += 1
        if n == 0:
            raise ValueError("RSS 中沒有解析到任何期別")
        store["updated"] = NOW.isoformat()
        save_json("invoice.json", store)
        log(f"[ok] 發票: 解析 {n} 期，共 {len(store['periods'])} 期")
    except Exception as e:  # noqa: BLE001
        log(f"[warn] 發票資料抓取失敗，使用快取: {e}")
    return store


def update_oil() -> dict:
    store = load_json("oil.json", {"current": None, "history": [], "updated": None})
    try:
        root = ET.fromstring(fetch(CPC_XML))
        prices, eff = {}, None
        for t in root.iter("Table"):
            kind = (t.findtext("型別名稱") or "").strip()
            name = (t.findtext("產品名稱") or "").strip()
            if kind != "汽柴油零售" or name not in FUELS:
                continue
            prices[name] = round(float(t.findtext("參考牌價_金額") or 0), 2)
            eff = roc_to_date(t.findtext("牌價生效日期") or "").isoformat()
        if len(prices) < 3 or not eff:
            raise ValueError(f"解析到的油品不足: {prices}")
        cur = {"effective": eff, "prices": prices}
        hist = [h for h in store.get("history", []) if h["effective"] != eff]
        hist.append(cur)
        hist.sort(key=lambda h: h["effective"], reverse=True)
        store = {"current": cur, "history": hist[:104], "updated": NOW.isoformat()}
        save_json("oil.json", store)
        log(f"[ok] 油價: {eff} {prices}")
    except Exception as e:  # noqa: BLE001
        log(f"[warn] 油價資料抓取失敗，使用快取: {e}")
    return store


def update_rates() -> dict:
    store = load_json("rates.json", {"rates": {}, "source_time": None, "history": [], "updated": None})
    try:
        j = json.loads(fetch(RATE_API))
        if j.get("result") != "success":
            raise ValueError(j)
        # API 給的是 1 TWD = x 外幣，轉成 1 外幣 = y TWD
        rates = {c: round(1 / j["rates"][c], 8) for c, *_ in CURRENCIES if j["rates"].get(c)}
        src_time = datetime.fromtimestamp(j["time_last_update_unix"], TW).isoformat()
        day = src_time[:10]
        hist = [h for h in store.get("history", []) if h["date"] != day]
        hist.append({"date": day, "rates": rates})
        hist.sort(key=lambda h: h["date"], reverse=True)
        store = {"rates": rates, "source_time": src_time, "history": hist[:90], "updated": NOW.isoformat()}
        save_json("rates.json", store)
        log(f"[ok] 匯率: {src_time} USD={rates.get('USD')} JPY={rates.get('JPY')}")
    except Exception as e:  # noqa: BLE001
        log(f"[warn] 匯率資料抓取失敗，使用快取: {e}")
    return store


# --------------------------------------------------------------------------- site helpers
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def site_url() -> str:
    if CFG.get("custom_domain"):
        return f"https://{CFG['custom_domain'].strip('/')}/"
    if CFG.get("site_url"):
        return CFG["site_url"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        owner, name = repo.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner.lower()}.github.io/"
        return f"https://{owner.lower()}.github.io/{name}/"
    return "http://localhost:8000/"


SITE = site_url()
PAGES: list[str] = []  # 給 sitemap 用


def head_extras() -> str:
    out = []
    if CFG.get("adsense_client"):
        c = esc(CFG["adsense_client"])
        out.append(
            f'<meta name="google-adsense-account" content="{c}">'
            f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={c}" '
            f'crossorigin="anonymous"></script>'
        )
    if CFG.get("ga_id"):
        g = esc(CFG["ga_id"])
        out.append(
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={g}"></script>'
            f"<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}"
            f"gtag('js',new Date());gtag('config','{g}');</script>"
        )
    return "\n".join(out)


def nav(rel: str, active: str) -> str:
    items = [
        ("", "首頁", "home"),
        ("invoice/", "發票對獎", "invoice"),
        ("oil/", "今日油價", "oil"),
        ("rate/", "匯率換算", "rate"),
        ("tools/", "生活工具", "tools"),
    ]
    on = ' class="on"'
    links = "".join(
        f'<a href="{rel}{href}"{on if key == active else ""}>{label}</a>' for href, label, key in items
    )
    return f'<nav class="nav">{links}</nav>'


def write_page(path: str, title: str, desc: str, body: str, active: str = "", data=None, jsonld=None) -> None:
    """path 例如 'index.html'、'invoice/index.html'、'rate/usd.html'"""
    depth = path.count("/")
    rel = "../" * depth
    url_path = path[:-10] if path.endswith("index.html") else path
    canonical = SITE + url_path
    PAGES.append(url_path)
    full_title = title if CFG["site_name"] in title else f"{title}｜{CFG['site_name']}"
    data_tag = (
        f'<script type="application/json" id="page-data">{json.dumps(data, ensure_ascii=False)}</script>'
        if data is not None
        else ""
    )
    ld_tag = (
        f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    )
    contact = (
        f' · <a href="mailto:{esc(CFG["contact_email"])}">聯絡我們</a>' if CFG.get("contact_email") else ""
    )
    doc = f"""<!doctype html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(full_title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{esc(CFG['site_name'])}">
<meta property="og:locale" content="zh_TW">
<meta name="theme-color" content="#0f766e">
<link rel="icon" href="{rel}favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{rel}style.css">
{head_extras()}
{ld_tag}
</head>
<body>
<header class="top">
  <div class="wrap">
    <a class="brand" href="{rel or './'}">{esc(CFG['site_name'])}</a>
    {nav(rel, active)}
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <p>資料來源：財政部稅務入口網、台灣中油、<a href="https://www.exchangerate-api.com" rel="nofollow noopener">Rates By Exchange Rate API</a>。
    本站資料僅供參考，實際內容請以官方公告為準。</p>
    <p><a href="{rel}about.html">關於本站</a> · <a href="{rel}privacy.html">隱私權政策</a>{contact}
    · 最後更新：{NOW.strftime('%Y/%m/%d %H:%M')}</p>
  </div>
</footer>
{data_tag}
<script src="{rel}app.js" defer></script>
</body>
</html>
"""
    out = DIST / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")


def faq_block(faqs: list[tuple[str, str]]) -> tuple[str, dict]:
    body = '<section class="card"><h2>常見問題</h2>' + "".join(
        f"<details><summary>{esc(q)}</summary><p>{a}</p></details>" for q, a in faqs
    ) + "</section>"
    ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", "", a)},
            }
            for q, a in faqs
        ],
    }
    return body, ld


# --------------------------------------------------------------------------- invoice pages
def period_label(p: dict) -> str:
    return f"{p['roc_year']}年{p['m1']}-{p['m2']}月"


def claim_window(p: dict) -> tuple[date, date, date]:
    y = p["roc_year"] + 1911
    dy, dm = add_months(y, p["m2"], 1)
    sy, sm = add_months(y, p["m2"], 2)
    ey, em = add_months(y, p["m2"], 5)
    return date(dy, dm, 25), date(sy, sm, 6), date(ey, em, 5)


def next_draw(today: date) -> date:
    y, m = today.year, today.month
    for i in range(0, 4):
        yy, mm = add_months(y, m, i)
        if mm % 2 == 1 and date(yy, mm, 25) >= today:
            return date(yy, mm, 25)
    return today


def invoice_numbers_html(p: dict) -> str:
    first = "".join(f'<span class="num">{n[:5]}<b>{n[5:]}</b></span>' for n in p["first"])
    extra_nums = "".join('<span class="num"><b>' + e + "</b></span>" for e in p.get("extra", []))
    extra = (
        f"<tr><th>增開六獎</th><td>{extra_nums}</td>"
        f"<td>末 3 碼相同 200 元</td></tr>"
        if p.get("extra")
        else ""
    )
    return f"""<table class="prize">
<tr><th>特別獎<small>1,000 萬元</small></th><td><span class="num big">{p['special']}</span></td><td>8 碼完全相同</td></tr>
<tr><th>特獎<small>200 萬元</small></th><td><span class="num big">{p['grand']}</span></td><td>8 碼完全相同</td></tr>
<tr><th>頭獎<small>20 萬元</small></th><td>{first}</td><td>8 碼相同 20 萬；末 7 碼 4 萬；末 6 碼 1 萬；末 5 碼 4 千；末 4 碼 1 千；<b>末 3 碼 200 元</b></td></tr>
{extra}
</table>"""


def checker_html() -> str:
    return """<div class="checker">
  <label for="inv-input">輸入發票號碼（輸入末 3 碼即可快速對獎，或輸入完整 8 碼）</label>
  <input id="inv-input" inputmode="numeric" autocomplete="off" maxlength="8" placeholder="末3碼">
  <p class="note">小技巧：對完 3 碼沒中會自動清空，可以一張接一張連續輸入。</p>
  <div id="inv-result" class="result" aria-live="polite">請輸入號碼</div>
  <ul id="inv-log" class="log"></ul>
</div>"""


INVOICE_FAQ = [
    ("統一發票怎麼對獎最快？",
     "先看末 3 碼：只要末 3 碼和任一組「頭獎」號碼的末 3 碼相同，就至少中六獎 200 元，再往前比對更多碼數。"
     "特別獎與特獎則必須 8 碼完全相同。用本頁的對獎工具輸入末 3 碼就能立即判斷。"),
    ("發票什麼時候開獎？",
     "統一發票每兩個月開獎一次，於單數月（1、3、5、7、9、11 月）的 25 日開出前兩個月的中獎號碼，遇假日照常開獎。"),
    ("中獎發票領獎期限是多久？",
     "自開獎日次月 6 日起，可領獎 3 個月。例如 7-8 月發票於 9 月 25 日開獎，領獎期間為 10 月 6 日至隔年 1 月 5 日。逾期視同放棄。"),
    ("中獎發票去哪裡領？",
     "4 千元以下（含）可至全台四大超商（7-11、全家、萊爾富、OK）或全聯等代發單位兌領；1 萬元以上需至指定金融機構（如郵局、台灣銀行、土地銀行等）兌領，"
     "並會預扣 20% 所得稅（5 千元以下免扣），每張另需繳印花稅 0.4%。"),
    ("雲端發票中獎怎麼領？",
     "雲端發票中獎會由財政部通知，若已在「統一發票兌獎 APP」或財政部電子發票平台設定領獎帳戶，獎金會自動匯入，不用另外跑超商。"),
]


def build_invoice(store: dict) -> list[dict]:
    periods = sorted(store["periods"].values(), key=lambda p: p["key"], reverse=True)
    if not periods:
        write_page("invoice/index.html", "統一發票對獎", "統一發票中獎號碼",
                   '<section class="card"><p>發票資料暫時無法取得，請稍後再試。</p></section>', "invoice")
        return []
    today = NOW.date()
    latest = periods[0]
    faq_html, faq_ld = faq_block(INVOICE_FAQ)

    def page_data(p):
        return {"type": "invoice", "special": p["special"], "grand": p["grand"], "first": p["first"],
                "extra": p.get("extra", [])}

    # 每期獨立頁面
    for i, p in enumerate(periods):
        draw, s, e = claim_window(p)
        status = "領獎中" if s <= today <= e else ("尚未開始領獎" if today < s else "已過領獎期限")
        others = "".join(
            f'<a class="chip{" on" if q["key"] == p["key"] else ""}" href="{q["key"]}.html">{period_label(q)}</a>'
            for q in periods[:12]
        )
        body = f"""
<h1>{period_label(p)}統一發票中獎號碼</h1>
<p class="lead">{fmt_date_roc(draw)}開獎 · 領獎期間 {fmt_date(s)} ～ {fmt_date(e)}（<b>{status}</b>）</p>
<section class="card">{checker_html()}</section>
<section class="card"><h2>{period_label(p)}中獎號碼</h2>{invoice_numbers_html(p)}
<p class="note">資料來源：<a href="{esc(p['source'])}" rel="nofollow noopener">財政部稅務入口網</a>，如有增開六獎或更正，以財政部公告為準。</p></section>
<section class="card"><h2>其他期別</h2><div class="chips">{others}</div></section>
{faq_html}"""
        write_page(
            f"invoice/{p['key']}.html",
            f"{period_label(p)}統一發票中獎號碼・快速對獎",
            f"{period_label(p)}統一發票中獎號碼：特別獎 {p['special']}、特獎 {p['grand']}、頭獎 {'、'.join(p['first'])}。"
            f"輸入末三碼立即對獎，領獎期限 {fmt_date(s)}～{fmt_date(e)}。",
            body, "invoice", page_data(p), faq_ld,
        )

    # 對獎首頁：最新一期 + 上一期（通常仍在領獎期）
    tabs = "".join(
        f'<button class="tab{" on" if i == 0 else ""}" data-period="{i}">{period_label(p)}</button>'
        for i, p in enumerate(periods[:2])
    )
    blocks = ""
    for i, p in enumerate(periods[:2]):
        draw, s, e = claim_window(p)
        blocks += f"""<div class="period" data-idx="{i}"{"" if i == 0 else " hidden"}>
<p class="lead">{fmt_date_roc(draw)}開獎 · 領獎期間 {fmt_date(s)} ～ {fmt_date(e)}</p>
{invoice_numbers_html(p)}</div>"""
    hist = "".join(f'<a class="chip" href="{p["key"]}.html">{period_label(p)}</a>' for p in periods)
    nd = next_draw(today)
    body = f"""
<h1>統一發票對獎｜{period_label(latest)}中獎號碼</h1>
<p class="lead">最新一期：{period_label(latest)}。下次開獎：{fmt_date_roc(nd)}（{(nd - today).days} 天後）。</p>
<section class="card"><div class="tabs">{tabs}</div>{checker_html()}</section>
<section class="card"><h2>中獎號碼</h2>{blocks}</section>
<section class="card"><h2>歷史期別</h2><div class="chips">{hist}</div></section>
<section class="card"><h2>獎金一覽</h2>
<table class="simple"><tr><th>獎別</th><th>中獎條件</th><th>獎金</th></tr>
<tr><td>特別獎</td><td>8 碼與特別獎相同</td><td>1,000 萬元</td></tr>
<tr><td>特獎</td><td>8 碼與特獎相同</td><td>200 萬元</td></tr>
<tr><td>頭獎</td><td>8 碼與頭獎相同</td><td>20 萬元</td></tr>
<tr><td>二獎</td><td>末 7 碼與頭獎相同</td><td>4 萬元</td></tr>
<tr><td>三獎</td><td>末 6 碼與頭獎相同</td><td>1 萬元</td></tr>
<tr><td>四獎</td><td>末 5 碼與頭獎相同</td><td>4,000 元</td></tr>
<tr><td>五獎</td><td>末 4 碼與頭獎相同</td><td>1,000 元</td></tr>
<tr><td>六獎</td><td>末 3 碼與頭獎相同</td><td>200 元</td></tr>
</table></section>
{faq_html}"""
    data = {"type": "invoice-multi", "periods": [page_data(p) for p in periods[:2]]}
    write_page(
        "invoice/index.html",
        f"統一發票對獎｜{period_label(latest)}中獎號碼・輸入末三碼快速對獎",
        f"{period_label(latest)}統一發票中獎號碼與線上對獎工具，輸入末三碼立即知道有沒有中獎，附領獎期限、領獎地點與獎金一覽，開獎當天自動更新。",
        body, "invoice", data, faq_ld,
    )
    return periods


# --------------------------------------------------------------------------- oil pages
OIL_FAQ = [
    ("油價什麼時候調整？",
     "台灣中油與台塑石化採浮動油價機制，每週日中午公布下週油價，<b>週一凌晨零時起生效</b>。"),
    ("油價是怎麼計算的？",
     "依「浮動油價機制」，以 70% 杜拜原油加 30% 布蘭特原油（7D3B）的週均價變動，並參考匯率、亞鄰最低價等因素調整，"
     "政府另有油價平穩措施，漲幅過大時會吸收部分漲幅。"),
    ("95 和 92 無鉛汽油怎麼選？",
     "請依車主手冊建議的辛烷值加油。一般國產車多半 92 或 95 即可，高壓縮比引擎或進口性能車才需要 95/98。加高於需求的辛烷值並不會增加動力。"),
    ("本頁的油價是哪個加油站的？",
     "本頁為台灣中油直營站的參考牌價（含稅），各加油站可能有不同折扣，台塑石化價格通常相同或相差 0.1 元。"),
]


def build_oil(store: dict) -> None:
    cur = store.get("current")
    faq_html, faq_ld = faq_block(OIL_FAQ)
    if not cur:
        write_page("oil/index.html", "今日油價", "今日油價查詢",
                   '<section class="card"><p>油價資料暫時無法取得。</p></section>' + faq_html, "oil")
        return
    hist = store.get("history", [])
    prev = hist[1] if len(hist) > 1 else None
    eff = date.fromisoformat(cur["effective"])
    cards = ""
    for f in FUELS:
        if f not in cur["prices"]:
            continue
        v = cur["prices"][f]
        diff = ""
        if prev and f in prev["prices"]:
            d = round(v - prev["prices"][f], 2)
            cls = "up" if d > 0 else ("down" if d < 0 else "same")
            sign = "▲" if d > 0 else ("▼" if d < 0 else "－")
            diff = f'<span class="diff {cls}">{sign} {abs(d):.1f}</span>'
        cards += f'<div class="stat"><div class="k">{f}</div><div class="v">{v:.1f}<small>元/公升</small></div>{diff}</div>'
    rows = "".join(
        "<tr><td>" + fmt_date(date.fromisoformat(h["effective"])) + "</td>"
        + "".join(f"<td>{h['prices'].get(f, 0):.1f}</td>" for f in FUELS)
        + "</tr>"
        for h in hist[:20]
    )
    options = "".join(f'<option value="{cur["prices"][f]}">{f}（{cur["prices"][f]:.1f}）</option>'
                      for f in FUELS if f in cur["prices"])
    today = NOW.date()
    nxt = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    body = f"""
<h1>今日油價｜{fmt_date_roc(eff)}起中油油價</h1>
<p class="lead">92 無鉛 {cur['prices'].get('92無鉛汽油', 0):.1f} 元、95 無鉛 {cur['prices'].get('95無鉛汽油', 0):.1f} 元、
98 無鉛 {cur['prices'].get('98無鉛汽油', 0):.1f} 元、超級柴油 {cur['prices'].get('超級柴油', 0):.1f} 元（每公升，{fmt_date(eff)} 生效）。
下次調價生效日：{fmt_date(nxt)}（週一）。</p>
<section class="stats">{cards}</section>
<section class="card"><h2>加滿一箱要多少錢？</h2>
<div class="form">
<label>油品 <select id="oil-type">{options}</select></label>
<label>加油量（公升）<input id="oil-l" type="number" inputmode="decimal" value="40" min="0"></label>
<label>或預算（元）<input id="oil-money" type="number" inputmode="decimal" placeholder="例如 1000" min="0"></label>
</div>
<div id="oil-result" class="result"></div></section>
<section class="card"><h2>近期油價紀錄</h2><div class="scroll"><table class="simple">
<tr><th>生效日</th>{"".join(f"<th>{f.replace('無鉛汽油', '無鉛')}</th>" for f in FUELS)}</tr>{rows}</table></div>
<p class="note">資料來源：台灣中油參考牌價（含稅），本站每天自動更新。</p></section>
{faq_html}"""
    write_page(
        "oil/index.html",
        f"今日油價查詢｜{fmt_date(eff)} 中油 92/95/98 汽油、柴油價格",
        f"{fmt_date(eff)}起中油油價：92無鉛 {cur['prices'].get('92無鉛汽油', 0):.1f} 元、95無鉛 {cur['prices'].get('95無鉛汽油', 0):.1f} 元、"
        f"98無鉛 {cur['prices'].get('98無鉛汽油', 0):.1f} 元、超級柴油 {cur['prices'].get('超級柴油', 0):.1f} 元。附加油金額試算與歷史油價。",
        body, "oil", {"type": "oil"}, faq_ld,
    )


# --------------------------------------------------------------------------- rate pages
RATE_FAQ = [
    ("本站匯率和銀行牌告匯率有什麼不同？",
     "本站顯示的是國際市場<b>中間價</b>，方便快速估算。銀行換匯時會有買入價、賣出價的價差，現金匯率價差通常更大，實際換匯請以銀行牌告為準。"),
    ("出國換外幣怎麼換比較划算？",
     "一般來說，使用「即期匯率」（例如外幣帳戶）比「現金匯率」划算；機場換匯通常較不划算。也可比較各銀行線上結匯、外幣提款機與海外刷卡回饋。"),
    ("匯率多久更新一次？",
     "本站每天自動更新多次匯率資料，頁面上會標示資料時間。"),
]


def build_rates(store: dict) -> None:
    rates = store.get("rates") or {}
    faq_html, faq_ld = faq_block(RATE_FAQ)
    if not rates:
        write_page("rate/index.html", "匯率換算", "匯率換算",
                   '<section class="card"><p>匯率資料暫時無法取得。</p></section>' + faq_html, "rate")
        return
    src_time = datetime.fromisoformat(store["source_time"])
    stamp = src_time.strftime("%Y/%m/%d %H:%M")
    hist = store.get("history", [])
    data = {"type": "rate", "rates": rates, "names": CUR_NAME}

    def converter(sel: str) -> str:
        opts = "".join(
            f'<option value="{c}"{" selected" if c == sel else ""}>{name} {c}</option>'
            for c, name, _, flag in CURRENCIES if c in rates
        )
        return f"""<div class="form conv">
<label>外幣 <select id="rate-cur">{opts}</select></label>
<label>外幣金額 <input id="rate-foreign" type="number" inputmode="decimal" value="100"></label>
<label>新台幣 TWD <input id="rate-twd" type="number" inputmode="decimal"></label>
</div><p class="note">資料時間：{stamp}（國際中間價，僅供參考）</p>"""

    table = "".join(
        f'<tr><td><a href="{c.lower()}.html">{name}（{c}）</a></td><td>{smart_rate(rates[c])}</td>'
        f"<td>{smart_rate(1 / rates[c])}</td></tr>"
        for c, name, _, flag in CURRENCIES if c in rates
    )
    body = f"""
<h1>匯率換算｜外幣兌新台幣即時試算</h1>
<p class="lead">美金 1 = {smart_rate(rates['USD'])} 台幣、日圓 1 = {smart_rate(rates['JPY'])} 台幣。輸入金額即可雙向換算。</p>
<section class="card">{converter('USD')}</section>
<section class="card"><h2>各國貨幣匯率表</h2><table class="simple">
<tr><th>幣別</th><th>1 外幣 = 台幣</th><th>1 台幣 = 外幣</th></tr>{table}</table></section>
{faq_html}"""
    write_page("rate/index.html", "匯率換算｜美金、日圓、韓元、歐元兌台幣即時試算",
               f"線上匯率換算工具，{len(rates)} 種外幣兌新台幣雙向試算：美金、日圓、韓元、人民幣、歐元、港幣、泰銖等，每日自動更新。",
               body, "rate", data, faq_ld)

    amounts_cache = {}
    for c, name, alias, flag in CURRENCIES:
        if c not in rates:
            continue
        r = rates[c]
        unit = 1000 if r < 0.05 else (100 if r < 0.5 else 1)
        base_amounts = [1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000, 100000]
        f_amounts = [a * unit for a in base_amounts][:10]
        t_amounts = [100, 500, 1000, 3000, 5000, 10000, 30000, 50000, 100000]
        amounts_cache[c] = f_amounts
        rows1 = "".join(f"<tr><td>{a:,} {name}</td><td>{fmt_num(a * r)} 台幣</td></tr>" for a in f_amounts)
        rows2 = "".join(f"<tr><td>{a:,} 台幣</td><td>{fmt_num(a / r, 2)} {name}</td></tr>" for a in t_amounts)
        trend = [(h["date"], h["rates"].get(c)) for h in hist[:30] if h["rates"].get(c)]
        trend_rows = "".join(f"<tr><td>{d}</td><td>{smart_rate(v)}</td></tr>" for d, v in trend[:14])
        trend_html = ""
        if len(trend) >= 2:
            hi = max(v for _, v in trend)
            lo = min(v for _, v in trend)
            trend_html = f"""<section class="card"><h2>近期{name}匯率走勢</h2>
<p>近 {len(trend)} 天最高 {smart_rate(hi)}、最低 {smart_rate(lo)}。</p>
<table class="simple"><tr><th>日期</th><th>1 {name} = 台幣</th></tr>{trend_rows}</table></section>"""
        others = "".join(
            f'<a class="chip" href="{x.lower()}.html">{nm}</a>' for x, nm, _, fl in CURRENCIES if x != c and x in rates
        )
        body = f"""
<h1>{name}匯率換算｜1 {name}等於多少台幣？</h1>
<p class="lead">{stamp} 參考匯率：<b>1 {name}（{c}）= {smart_rate(r)} 新台幣</b>，1 新台幣 = {smart_rate(1 / r)} {name}。</p>
<section class="card">{converter(c)}</section>
<div class="grid2">
<section class="card"><h2>{name}換台幣</h2><table class="simple">{rows1}</table></section>
<section class="card"><h2>台幣換{name}</h2><table class="simple">{rows2}</table></section>
</div>
{trend_html}
<section class="card"><h2>其他幣別</h2><div class="chips">{others}</div></section>
{faq_html}"""
        example = f_amounts[4]
        write_page(
            f"rate/{c.lower()}.html",
            f"{name}匯率換算｜{c} 兌台幣・{alias}換台幣即時試算",
            f"今日{name}（{alias}）匯率：1 {c} = {smart_rate(r)} 台幣，{example:,} {name}約 {fmt_num(example * r, 0)} 台幣。"
            f"{name}台幣雙向換算工具與換算表，每日自動更新。",
            body, "rate", data, faq_ld,
        )


# --------------------------------------------------------------------------- tool pages
def build_tools() -> None:
    tools = [
        ("ping.html", "坪數換算", "坪、平方公尺、平方英尺互換，買房租屋必備。"),
        ("roc-year.html", "民國西元換算", "民國年、西元年、生肖、年齡對照一次查。"),
        ("salary.html", "時薪月薪換算", "時薪、日薪、月薪、年薪互相換算，附基本工資參考。"),
    ]
    cards = "".join(
        f'<a class="card link" href="{f}"><h2>{t}</h2><p>{d}</p></a>' for f, t, d in tools
    )
    write_page("tools/index.html", "生活工具", "坪數換算、民國西元換算、時薪月薪換算等免費線上生活工具。",
               f"<h1>生活工具</h1><div class='grid2'>{cards}</div>", "tools")

    ping_body = """
<h1>坪數換算｜坪、平方公尺、平方英尺互換</h1>
<p class="lead">1 坪 = 3.3058 平方公尺 = 35.583 平方英尺；1 平方公尺 = 0.3025 坪。輸入任一欄位即自動換算。</p>
<section class="card"><div class="form conv">
<label>坪 <input id="u-ping" data-unit="ping" class="unit" type="number" inputmode="decimal" value="30"></label>
<label>平方公尺 m² <input id="u-m2" data-unit="m2" class="unit" type="number" inputmode="decimal"></label>
<label>平方英尺 ft² <input id="u-ft2" data-unit="ft2" class="unit" type="number" inputmode="decimal"></label>
</div></section>
<section class="card"><h2>坪數換算表</h2><table class="simple"><tr><th>坪</th><th>平方公尺</th><th>平方英尺</th></tr>
""" + "".join(
        f"<tr><td>{p}</td><td>{p * 3.305785:.2f}</td><td>{p * 35.5832:.1f}</td></tr>"
        for p in [1, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 80, 100]
    ) + """</table></section>
<section class="card"><h2>坪數小知識</h2>
<p><b>權狀坪數</b>包含主建物、附屬建物（陽台、雨遮）與公共設施；<b>室內實際坪數</b>通常只有權狀坪數的 6～7 成（公設比 30～40%）。
看房時建議問清楚主建物坪數與公設比，再用上方工具換算成平方公尺，比較容易想像空間大小。</p>
<p>一般參考：一張雙人床約 0.6 坪；一個 3 坪房間約 3.3 m × 3 m。</p></section>"""
    write_page("tools/ping.html", "坪數換算｜坪、平方公尺、平方英尺線上換算",
               "坪數換算工具：1 坪等於 3.3058 平方公尺。坪、平方公尺、平方英尺雙向即時換算，附坪數換算表與權狀坪數說明。",
               ping_body, "tools", {"type": "ping"})

    animals = "鼠牛虎兔龍蛇馬羊猴雞狗豬"
    this_year = NOW.year
    rows = "".join(
        f"<tr><td>{y}</td><td>{'民國' + str(y - 1911) if y > 1911 else '民國前' + str(1912 - y)}</td>"
        f"<td>{animals[(y - 4) % 12]}</td><td>{this_year - y} 歲</td></tr>"
        for y in range(this_year, 1939, -1)
    )
    roc_body = f"""
<h1>民國西元換算｜民國年、西元年、生肖、年齡對照表</h1>
<p class="lead">西元年 − 1911 = 民國年；民國年 + 1911 = 西元年。今年是西元 {this_year} 年＝民國 {this_year - 1911} 年（{animals[(this_year - 4) % 12]}年）。</p>
<section class="card"><div class="form conv">
<label>民國年 <input id="y-roc" type="number" inputmode="numeric" value="{this_year - 1911}"></label>
<label>西元年 <input id="y-ad" type="number" inputmode="numeric" value="{this_year}"></label>
</div><div id="y-result" class="result"></div></section>
<section class="card"><h2>年齡生肖對照表</h2><p class="note">年齡為「今年生日過後」的足歲。生肖以農曆年為準，1～2 月出生者請留意農曆新年日期。</p>
<div class="scroll"><table class="simple"><tr><th>西元</th><th>民國</th><th>生肖</th><th>今年滿</th></tr>{rows}</table></div></section>"""
    write_page("tools/roc-year.html", f"民國西元換算｜{this_year} 年是民國幾年？年齡生肖對照表",
               f"民國西元年換算工具：{this_year} 年是民國 {this_year - 1911} 年。附民國、西元、生肖、年齡完整對照表。",
               roc_body, "tools", {"type": "roc"})

    salary_body = """
<h1>時薪月薪換算｜時薪、日薪、月薪、年薪試算</h1>
<p class="lead">輸入任一種薪資，自動換算其他薪資。預設每日工作 8 小時、每月 21.75 個工作天（勞基法週休二日平均）。</p>
<section class="card"><div class="form conv">
<label>時薪 <input id="s-hour" class="sal" data-k="hour" type="number" inputmode="decimal" value="200"></label>
<label>日薪 <input id="s-day" class="sal" data-k="day" type="number" inputmode="decimal"></label>
<label>月薪 <input id="s-month" class="sal" data-k="month" type="number" inputmode="decimal"></label>
<label>年薪（12 個月）<input id="s-year" class="sal" data-k="year" type="number" inputmode="decimal"></label>
</div>
<div class="form"><label>每日工時 <input id="s-hpd" type="number" value="8"></label>
<label>每月工作天 <input id="s-dpm" type="number" value="21.75" step="0.25"></label></div></section>
<section class="card"><h2>說明</h2>
<p>勞動部計算加班費時，月薪換算時薪的方式是「月薪 ÷ 30 ÷ 8」，與本工具「實際工作天」估算不同，若要計算加班費請以勞動部規定為準。
基本工資每年由勞動部公告調整，請以勞動部最新公告為準。</p></section>"""
    write_page("tools/salary.html", "時薪月薪換算｜時薪、日薪、月薪、年薪線上試算",
               "時薪換月薪、月薪換時薪線上試算工具，可自訂每日工時與每月工作天數，打工族、上班族必備。",
               salary_body, "tools", {"type": "salary"})


# --------------------------------------------------------------------------- home + misc
def build_home(inv: list[dict], oil: dict, rates: dict) -> None:
    parts = []
    if inv:
        p = inv[0]
        parts.append(f"""<a class="card link" href="invoice/"><h2>🧾 發票對獎・{period_label(p)}</h2>
<p>特別獎 <span class="num">{p['special']}</span><br>特獎 <span class="num">{p['grand']}</span><br>
頭獎 {'、'.join(f'<span class="num">{n}</span>' for n in p['first'])}</p><p class="more">輸入末三碼快速對獎 →</p></a>""")
    if oil.get("current"):
        c = oil["current"]
        lines = "".join(f"{f.replace('無鉛汽油', '無鉛')} <b>{c['prices'][f]:.1f}</b>　" for f in FUELS[:3] if f in c["prices"])
        parts.append(f"""<a class="card link" href="oil/"><h2>⛽ 今日油價</h2>
<p>{lines}<br>超級柴油 <b>{c['prices'].get('超級柴油', 0):.1f}</b></p><p class="note">{fmt_date(date.fromisoformat(c['effective']))} 生效</p>
<p class="more">加油金額試算 →</p></a>""")
    if rates.get("rates"):
        r = rates["rates"]
        lines = "<br>".join(f"{CUR_NAME[c]} <b>{smart_rate(r[c])}</b>" for c in ["USD", "JPY", "KRW", "EUR", "CNY"] if c in r)
        parts.append(f"""<a class="card link" href="rate/"><h2>💱 今日匯率（兌台幣）</h2><p>{lines}</p>
<p class="more">匯率換算 →</p></a>""")
    parts.append("""<a class="card link" href="tools/"><h2>🧰 生活工具</h2>
<p>坪數換算 · 民國西元換算 · 時薪月薪換算</p><p class="more">更多工具 →</p></a>""")
    body = f"""<h1>{esc(CFG['site_name'])}</h1><p class="lead">{esc(CFG['site_tagline'])}</p>
<div class="grid2">{''.join(parts)}</div>"""
    ld = {"@context": "https://schema.org", "@type": "WebSite", "name": CFG["site_name"], "url": SITE}
    write_page("index.html", f"{CFG['site_name']}｜發票對獎、今日油價、匯率換算", CFG["site_tagline"] + "。",
               body, "home", None, ld)


def build_static_pages() -> None:
    email = CFG.get("contact_email") or ""
    contact = f'如有任何問題或建議，歡迎來信：<a href="mailto:{esc(email)}">{esc(email)}</a>。' if email else ""
    write_page("about.html", "關於本站", f"關於{CFG['site_name']}",
               f"""<h1>關於本站</h1><section class="card">
<p>{esc(CFG['site_name'])}整理台灣人日常最常查詢的生活資訊：統一發票中獎號碼、中油油價、外幣匯率，並提供對獎、加油試算、匯率換算、坪數換算等免費小工具。</p>
<p>所有資料皆由程式自動從官方或公開來源取得並每天更新，力求正確，但仍可能因來源更新延遲而有落差，重要決定請以官方公告為準。</p>
<p>{contact}</p></section>""")
    write_page("privacy.html", "隱私權政策", f"{CFG['site_name']}隱私權政策",
               f"""<h1>隱私權政策</h1><section class="card">
<p>本站不要求使用者註冊，也不會蒐集您的姓名、電話等個人資料。您在工具中輸入的發票號碼、金額等內容只在您的瀏覽器中運算，不會傳送到我們的伺服器。</p>
<h2>Cookie 與廣告</h2>
<p>本站使用 Google AdSense 等第三方廣告服務。第三方供應商（包括 Google）會使用 Cookie，根據使用者先前造訪本網站或其他網站的紀錄放送廣告。
Google 使用廣告 Cookie，可讓 Google 及其合作夥伴根據使用者造訪本站和（或）網際網路上其他網站的情形放送廣告。</p>
<p>您可以前往 <a href="https://adssettings.google.com" rel="nofollow noopener">Google 廣告設定</a> 停用個人化廣告，或前往
<a href="https://www.aboutads.info" rel="nofollow noopener">www.aboutads.info</a> 停用第三方供應商的個人化廣告 Cookie。</p>
<h2>流量分析</h2><p>本站可能使用 Google Analytics 統計匿名的瀏覽數據，以改善網站內容。</p>
<h2>政策修訂</h2><p>本政策如有修改，將公布於本頁。最後更新：{NOW.strftime('%Y/%m/%d')}。</p>
<p>{contact}</p></section>""")
    write_page("404.html", "找不到頁面", "找不到頁面",
               f'<h1>找不到這個頁面</h1><section class="card"><p><a href="{esc(SITE)}">回到首頁</a></p></section>')
    PAGES.remove("404.html")


def build_meta_files() -> None:
    today = NOW.date().isoformat()
    urls = "".join(f"<url><loc>{esc(SITE + p)}</loc><lastmod>{today}</lastmod></url>" for p in PAGES)
    (DIST / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        encoding="utf-8")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE}sitemap.xml\n", encoding="utf-8")
    if CFG.get("adsense_client"):
        pub = CFG["adsense_client"].replace("ca-", "")
        (DIST / "ads.txt").write_text(f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n", encoding="utf-8")
    if CFG.get("custom_domain"):
        (DIST / "CNAME").write_text(CFG["custom_domain"].strip("/") + "\n", encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")


# --------------------------------------------------------------------------- check
def check_freshness() -> int:
    problems = []
    now = NOW
    rates = load_json("rates.json", {})
    oil = load_json("oil.json", {})
    inv = load_json("invoice.json", {})
    for name, store, max_days in [("匯率", rates, 3), ("油價", oil, 10), ("發票", inv, 10)]:
        u = store.get("updated")
        if not u:
            problems.append(f"{name}: 沒有資料")
            continue
        age = (now - datetime.fromisoformat(u)).total_seconds() / 86400
        if age > max_days:
            problems.append(f"{name}: 已 {age:.1f} 天沒有成功更新")
    # 發票：開獎後 3 天應該要有新一期
    periods = sorted((inv.get("periods") or {}).keys())
    if periods:
        latest = inv["periods"][periods[-1]]
        draw, _, _ = claim_window(latest)
        today = now.date()
        nd = next_draw(draw + timedelta(days=1))
        if today > nd + timedelta(days=3):
            problems.append(f"發票: 最新一期 {periods[-1]}，但 {nd} 應已開出新一期")
    if problems:
        log("資料過舊，請檢查:\n  " + "\n  ".join(problems))
        return 1
    log("[ok] 所有資料都是最新的")
    return 0


# --------------------------------------------------------------------------- main
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--check" in sys.argv:
        return check_freshness()
    offline = "--offline" in sys.argv
    if offline:
        inv = load_json("invoice.json", {"periods": {}})
        oil = load_json("oil.json", {})
        rates = load_json("rates.json", {})
    else:
        inv, oil, rates = update_invoice(), update_oil(), update_rates()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for f in STATIC.iterdir():
        if f.is_file():
            shutil.copy2(f, DIST / f.name)

    periods = build_invoice(inv)
    build_oil(oil)
    build_rates(rates)
    build_tools()
    build_home(periods, oil, rates)
    build_static_pages()
    build_meta_files()
    log(f"[ok] 網站產生完成：{len(PAGES)} 頁 → {DIST}（網址 {SITE}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
