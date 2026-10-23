#!/usr/bin/env python3
"""为冻结的校准卡片注入"要价已变动"横幅。

卡片是建卡时点快照(2026-09-20 等);挂牌降价/重挂后卡片要价与
watchlist latest_asking 脱节。本脚本扫描全部卡片,凡卡片要价 !=
watchlist.latest_asking 的,在 <h1> 后注入价格历史横幅。
幂等:已有横幅则先移除旧横幅再按最新数据重写。
只写 banner,不动卡片其余内容;只读 watchlist。
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH = ROOT / "data/research/watchlist/property-watchlist.json"
CARD_GLOB = ROOT / "reports/calibration"
BANNER_RE = re.compile(
    r'<div id="price-drift-banner".*?</div>\s*', re.S)


def watch_index():
    w = json.loads(WATCH.read_text())
    out = {}
    for e in w["entries"]:
        prices = [(s.get("scan"), s.get("asking")) for s in
                  e.get("sightings", []) if s.get("asking")]
        out[e["address"]] = {
            "latest": e.get("latest_asking"),
            "last_seen": e.get("last_seen"),
            "history": prices,
            "events": e.get("events") or [],
        }
    return out


def fmt(n):
    return f"${n:,.0f}" if n else "?"


def banner_html(entry, card_asking):
    hist = " → ".join(fmt(p) for _, p in entry["history"])
    delta = (entry["latest"] or 0) - (card_asking or 0)
    arrow = "↓" if delta < 0 else "↑" if delta > 0 else "="
    return (
        f'<div id="price-drift-banner" style="background:#fff3cd;'
        f'border:1px solid #e0a800;padding:8px 12px;margin:8px 0;'
        f'font-size:14px;border-radius:6px">'
        f'<b>⚠ 要价已变动</b> — 本卡建卡价 {fmt(card_asking)};'
        f'最新要价 <b>{fmt(entry["latest"])}</b>({entry["last_seen"]})'
        f' {arrow} {fmt(abs(delta))};历史: {hist}.'
        f' V3s/ROI 使用的是最新要价。</div>\n')


def process(path, widx):
    html = path.read_text(encoding="utf-8")
    m = re.search(r"<h1>([^<]+)</h1>", html)
    if not m:
        return None
    addr = m.group(1).strip().upper()
    entry = widx.get(addr)
    if not entry or not entry["latest"]:
        return None
    ma = re.search(r"Asking[^\d]*([\d,]+)", html)
    card_asking = int(ma.group(1).replace(",", "")) if ma else None
    html = BANNER_RE.sub("", html)          # 幂等:清旧横幅
    if card_asking == entry["latest"]:
        path.write_text(html, encoding="utf-8")
        return "in_sync"
    if "</header>" in html:
        html = html.replace("</header>", "</header>\n" +
                            banner_html(entry, card_asking), 1)
    else:
        html = html.replace(m.group(0), m.group(0) + "\n" +
                            banner_html(entry, card_asking), 1)
    path.write_text(html, encoding="utf-8")
    return "patched"


def main():
    widx = watch_index()
    stats = {"patched": 0, "in_sync": 0, "no_entry": 0, "files": 0}
    for f in sorted(CARD_GLOB.glob("*/cards/*.html")):
        stats["files"] += 1
        r = process(f, widx)
        stats[r or "no_entry"] += 1
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
