#!/usr/bin/env python3
"""把已完成人工标注迁移进校准卡片 — 2026-10-22

卡片原生只从 localStorage 读已保存标注 → 服务端静态卡永远是空。
本脚本把 strategy-v2-human-annotations-*.json(最新合并文件)中
每条房源的 human_set_fields 字段注入对应卡页为 const SEEDED;
页面 JS 在 localStorage 无记录时以 SEEDED 初始化 state
(localStorage 有记录时仍优先 — 不覆盖用户当场改判)。

幂等: 重复运行会先移除旧 SEEDED 再注入最新标注。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 全部人工标注文件(主 + newbatch*),按文件名内日期取最新一条 per key
ANN_FILES = sorted(
    (ROOT / "data/research/annotations")
    .glob("strategy-v2-human-annotations*.json"),
    key=lambda p: re.search(r"(\d{4}-\d{2}-\d{2})", p.name).group(1))
CARDS_GLOB = "reports/calibration/*/cards/mls-*.html"

MARK = "/* SEEDED_HUMAN */"


def main() -> None:
    records = {}
    for f in ANN_FILES:                     # 后写覆盖 → 最新日期赢
        for r in json.loads(f.read_text())["annotations"]:
            if r.get("property_key"):
                records[r["property_key"]] = r
    patched = skipped = noann = 0
    for card in sorted(ROOT.glob(CARDS_GLOB)):
        key = "mls:" + card.stem.split("-", 1)[1]      # mls-R3165202 → mls:R3165202
        html = card.read_text()
        # 幂等: 先摘除旧 SEEDED 注入
        html = re.sub(r"\n?\s*const SEEDED = \{[^;]*\};[^\n]*" + re.escape(MARK) + r"[^\n]*",
                      "", html)
        html = re.sub(
            r"\n *(?:if \(!Object\.keys\(state\)\.length\) \{\n *)?HUMAN_FIELDS\.forEach\(f => \{\n"
            r" *const v = SEEDED\[f\];\n"
            r" *if \(v != null && !\(Array\.isArray\(v\) && !v\.length\) && v !== '' \)"
            r"(?: && state\[f\] == null)? state\[f\] = v;\n"
            r" *\}\);\n?(?: *\})?",
            "", html)
        rec = records.get(key)
        if not rec:
            card.write_text(html)
            noann += 1
            continue
        set_fields = rec.get("human_set_fields")
        if not set_fields:      # 无溯源: 以与 auto_snapshot 的差异字段为人工字段
            snap = rec.get("auto_snapshot") or {}
            set_fields = [f for f, v in rec.items()
                          if f.startswith("human_") and v not in (None, "", [])
                          and v != snap.get(f)]
        seed = {f: rec[f] for f in set_fields if f in rec and rec[f] not in (None, "", [])
                or (f == "human_must_keep" and rec.get(f))}
        inject = ("\n      HUMAN_FIELDS.forEach(f => {\n"
                  "        const v = SEEDED[f];\n"
                  "        if (v != null && !(Array.isArray(v) && !v.length) && v !== '' "
                  "&& state[f] == null) state[f] = v;\n"
                  "      });")
        seed_js = f"\n    const SEEDED = {json.dumps(seed, ensure_ascii=False)}; {MARK}"
        anchor = "    let state = {};"
        if anchor not in html:
            skipped += 1
            continue
        html = html.replace(anchor, seed_js + "\n" + anchor, 1)
        catch_anchor = "} catch (e) { state = {}; }"
        if catch_anchor not in html:
            skipped += 1
            continue
        html = html.replace(catch_anchor, catch_anchor + inject, 1)
        card.write_text(html)
        patched += 1
    print(f"patched={patched} no_annotation={noann} skipped={skipped}")


if __name__ == "__main__":
    sys.exit(main())
