#!/usr/bin/env python3
"""要价变动 ACTIVE 房源专审卡组 — 与原校准卡完全同款渲染。

做法: 从各批次 shadow/enrichment/photo-evidence 文件找回这 41 套的
原始行,用 build_card_context + write_packet 重出一套卡;
Asking 覆盖为 watchlist latest_asking,并在 </header> 后注入
价格变动横幅(含完整历史)。只写新目录,不改旧卡。
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from northshore_watchdog.research.strategy_v2_calibration import (  # noqa: E402
    build_card_context, write_packet)
from build_calibration_packet import (  # noqa: E402
    _candidates_by_key, _photo_manifest, _rows_by_key)
import refresh_stale_cards  # noqa: E402

OUTD = ROOT / "reports/calibration/price-review-2026-10-23"
NORM = ROOT / "data/research/normalized"

V3S = json.loads((NORM / "valuation-v3s-shadow-2026-10-22.json").read_text())
WATCH = json.loads((ROOT / "data/research/watchlist/"
                    "property-watchlist.json").read_text())

# 1) 41 套变价 ACTIVE + 价格历史
va = {s["address"]: s for s in V3S["sites"] if s["status"] == "ACTIVE"}
changed = []
for e in WATCH["entries"]:
    a = e["address"]
    if a not in va:
        continue
    ps = [(s.get("scan"), s.get("asking")) for s in e.get("sightings", [])
          if s.get("asking")]
    if len({p for _, p in ps}) < 2:
        continue
    changed.append({"addr": a, "entry": e, "hist": ps,
                    "latest": e.get("latest_asking")})
changed.sort(key=lambda x: x["hist"][-1][1] / x["hist"][0][1])  # 降幅大→小

# 2) 逐套找回最新批次 shadow row(新批优先,候选同理)
SHADOW_FILES = [
    "strategy-v2-shadow-unrated-2026-10-20.json",
    "strategy-v2-shadow-newbatch-2026-10-20.json",
    "strategy-v2-shadow-newbatch-2026-10-14.json",
    "strategy-v2-shadow-newbatch-2026-10-05.json",
    "strategy-v2-shadow-newbatch-2026-09-20.json",
    "strategy-v2-shadow-2026-09-09.json",
]
ENR_FILES = [
    "regional-official-enrichment-unrated-2026-10-20.json",
    "regional-official-enrichment-newbatch-2026-10-20.json",
    "regional-official-enrichment-2026-10-20.json",
    "regional-official-enrichment-newbatch-2026-10-14.json",
    "regional-official-enrichment-2026-10-14.json",
    "regional-official-enrichment-newbatch-2026-10-05.json",
    "regional-official-enrichment-2026-10-05.json",
    "regional-official-enrichment-2026-09-20.json",
]
PHOTO_FILES = [
    "strategy-v2-online-photo-evidence-unrated-2026-10-20.json",
    "strategy-v2-online-photo-evidence-newbatch-2026-10-20.json",
    "strategy-v2-online-photo-evidence-newbatch-2026-10-14.json",
    "strategy-v2-online-photo-evidence-newbatch-2026-10-05.json",
    "strategy-v2-online-photo-evidence-newbatch-2026-09-20.json",
    "strategy-v2-online-photo-evidence-2026-09-12.json",
]
AUTO_FILES = [
    "strategy-v2-auto-annotations-unrated-2026-10-20.json",
    "strategy-v2-auto-annotations-newbatch-2026-10-20.json",
    "strategy-v2-auto-annotations-newbatch-2026-10-14.json",
    "strategy-v2-auto-annotations-newbatch-2026-10-05.json",
    "strategy-v2-auto-annotations-newbatch-2026-09-20.json",
    "strategy-v2-auto-annotations-2026-09-20.json",
    "strategy-v2-auto-annotations-2026-09-18.json",
    "strategy-v2-auto-annotations-2026-09-17.json",
]
shadow_by_addr, cand_by_key, photo_by_key, auto_by_key = {}, {}, {}, {}
for f in SHADOW_FILES:
    p = NORM / f
    if p.exists():
        for k, r in _rows_by_key(p).items():
            shadow_by_addr.setdefault(r.get("address"), (k, r))
for f in ENR_FILES:
    p = NORM / f
    if p.exists():
        for k, r in _candidates_by_key(p).items():
            cand_by_key.setdefault(k, r)
for f in PHOTO_FILES:
    p = NORM / f
    if p.exists():
        for k, r in _rows_by_key(p).items():
            photo_by_key.setdefault(k, r)
for f in AUTO_FILES:                       # 自动预填(auto-prefill)
    p = NORM / f
    if not p.exists():
        continue
    recs = json.loads(p.read_text())
    recs = recs.get("annotations") or recs
    if isinstance(recs, dict):
        for k, v in recs.items():
            if isinstance(v, dict):
                auto_by_key.setdefault(k, v)

# 3) 建 contexts(同一卡组内 prev/next)
contexts, miss = [], []
for i, c in enumerate(changed):
    got = shadow_by_addr.get(c["addr"])
    if not got:
        miss.append(c["addr"]); continue
    key, srow = got
    first_ask = c["hist"][0][1]
    cand = dict(cand_by_key.get(key) or {})
    cand["asking"] = c["latest"]            # 覆盖为最新要价
    auto = dict(auto_by_key.get(key) or {})
    # 数据预填叙述里的 asking/BCA 比率用最新价重算(其余行不动)
    m_old = None
    for i, ln in enumerate(auto.get("reasons") or []):
        if ln.startswith("asking ") and "BCA估价" in ln:
            m_old = i
            break
    if m_old is not None:
        ln = auto["reasons"][m_old]
        nums = [float(x.replace(",", "")) for x in
                re.findall(r"\$([\d,]+)", ln)]
        if len(nums) >= 4:
            ask0, bca_t, bca_l, implied0 = nums[:4]
            bca_i = ask0 - implied0                    # 派生 BCA 建筑值
            implied = c["latest"] - bca_i
            auto["reasons"][m_old] = (
                f"asking ${c['latest']:,.0f} vs BCA估价 ${bca_t:,.0f}"
                f"（{c['latest']/bca_t:.2f}×）；BCA土地 ${bca_l:,.0f}"
                f"（{c['latest']/bca_l:.2f}×）；隐含地价=asking−建筑 "
                f"${implied:,.0f}（{bca_l/implied if implied else 0:.2f}×）"
                f"〔价格更新:{first_ask:,.0f}→{c['latest']:,.0f}〕")
    ctx = build_card_context(
        shadow_row=srow, candidate=cand,
        photo_manifest=_photo_manifest(key, photo_by_key.get(key)),
        split="calibration",
        prev_key=(shadow_by_addr[changed[i-1]["addr"]][0] if i else None),
        next_key=(shadow_by_addr[changed[i+1]["addr"]][0]
                  if i+1 < len(changed) else None),
        assets_from_dir=(OUTD / "cards").resolve(),
        auto=auto)
    ctx["_price_hist"] = c["hist"]          # 供注入横幅
    contexts.append(ctx)

summary = write_packet(OUTD, contexts, generated_at="2026-10-23")
print("cards:", len(contexts), "missing shadow:", miss,
      "auto_prefilled:", sum(1 for c in contexts if c.get("auto")))

# 卡组 JSON 导出(表格行同 render_price_changes 口径)
jrows = []
for c in changed:
    s = va[c["addr"]]
    e = c["entry"]
    ps = c["hist"]
    jrows.append({
        "address": c["addr"], "comm": s.get("comm"), "zone": s.get("zone"),
        "lot": s.get("lot"), "tier": s.get("tier"),
        "asking_latest": c["latest"],
        "asking_first": ps[0][1], "first_seen": ps[0][0],
        "last_seen": e.get("last_seen"),
        "history": [{"date": d, "asking": p} for d, p in ps],
        "delta": c["latest"] - ps[0][1],
        "delta_pct": round(c["latest"] / ps[0][1] - 1, 4),
        "v3s_ask": s["ask"], "v3s_eq_latest": s["ask"] == c["latest"],
        "best_plan": s.get("best_ref"),
        "mls": e.get("latest_mls"),
        "realtor_url": e.get("latest_source_url")})
(OUTD / "price-changes.json").write_text(json.dumps(
    {"generated": "2026-10-23", "n": len(jrows),
     "note": "V3s/ROI 基于 latest_asking;tier 人工复核后写回 watchlist",
     "rows": jrows}, ensure_ascii=False, indent=1))

# index.html 内嵌数据 + 导出按钮(服务只放 html,不依赖 .json 路径)
idx = OUTD / "index.html"
h = idx.read_text(encoding="utf-8")
payload = json.dumps({"generated": "2026-10-23", "rows": jrows},
                     ensure_ascii=False)
inject = (f"<script>const PRICE_REVIEW={payload};"
          "function exportReview(){const b=new Blob([JSON.stringify("
          "PRICE_REVIEW,null,1)],{type:'application/json'});"
          "const a=document.createElement('a');a.href=URL.createObjectURL(b);"
          "a.download='price-review-2026-10-23.json';a.click();}</script>"
          '<button onclick="exportReview()" style="position:fixed;top:8px;'
          'right:120px;z-index:9;padding:6px 14px;border-radius:8px;'
          'border:1px solid #0a7aff;background:#0a7aff;color:#fff">'
          '导出审查数据 JSON</button>')
h = h.replace("<body>", "<body>\n" + inject, 1)
idx.write_text(h, encoding="utf-8")

# 4) 卡片后处理:注入价格变动横幅(本卡价=建卡原始价,最新=watchlist)
hist_by_addr = {c["addr"]: c for c in changed}
for ctx in contexts:
    f = OUTD / "cards" / f"{ctx['slug']}.html"
    html = f.read_text(encoding="utf-8")
    c = hist_by_addr.get(ctx["address"])
    if not c:
        continue
    ps = c["hist"]
    first, latest = ps[0], ps[-1]
    delta = latest[1] - first[1]
    hist_txt = " → ".join(f"${p:,.0f}" for _, p in ps)
    banner = (
        f'<div id="price-drift-banner" style="background:#fff3cd;'
        f'border:1px solid #e0a800;padding:8px 12px;margin:8px 0;'
        f'font-size:14px;border-radius:6px">'
        f'<b>⚠ 要价变动 {delta:+,.0f}({delta/first[1]:+.1%})</b> — '
        f'历史: {hist_txt}({first[0]}~{latest[0]});'
        f'本卡 Asking 已为最新价 ${latest[1]:,.0f};'
        f'auto 预填分档基于建卡时旧价快照,请人工复核。</div>\n')
    html = html.replace("</header>", "</header>\n" + banner, 1)
    f.write_text(html, encoding="utf-8")

print(str(OUTD / "index.html"))
