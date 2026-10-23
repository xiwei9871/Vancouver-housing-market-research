#!/usr/bin/env bash
# V1.4.4 listing_map.html interaction regression — requires the
# playwright-cli browser + a file server on :8766 serving the repo
# root (AGENTS.md). Asserts sort/filter/map linkage stays consistent
# after repeated re-sorts (the data-k / META index binding fix).
set -euo pipefail
URL="http://localhost:8766/reports/audits/listing_level_valuation_v1_4/listing_map.html"
playwright-cli goto "$URL" >/dev/null
playwright-cli eval "() => {
  const R = {};
  const vis = () => Array.from(document.querySelectorAll('#proptable tbody tr')).filter(r => r.style.display !== 'none').map(r => r.querySelector('td').innerText.split('\\n')[0].replace(/<[^>]+>/g,'').trim());
  const roiCol = document.querySelectorAll('#proptable th').length - 1;
  const sel = document.querySelector('tr.filters select[data-kind=roi]');
  const setBand = v => { sel.value=v; sel.dispatchEvent(new Event('change')); };
  const clickSort = (i,n) => { for(let k=0;k<n;k++) sortBy(i); };
  R.repro = {asc:  (clickSort(roiCol,1), setBand('>30'), vis()),
             desc: (clickSort(roiCol,1), setBand('>30'), vis()),
             multi:(clickSort(roiCol,2), setBand('>30'), vis())};
  R.bands = {};
  for (const b of ['>30','20-30','10-20','0-10','<0','—','']) {
    setBand(b); R.bands[b||'all'] = vis().length;
  }
  const cs = document.querySelector('tr.filters select');
  const c0 = Array.from(cs.options).map(o=>o.value).filter(v=>v)[0];
  cs.value = c0; cs.dispatchEvent(new Event('change'));
  R.combo_comm = {comm: c0, n: vis().length};
  document.querySelectorAll('#xbar select').forEach(e=>{
    if(e.dataset.x==='prod'){e.value='DUPLEX';e.dispatchEvent(new Event('change'));}});
  R.combo_comm_duplex = {comm: c0, n: vis().length};
  R.mapKeys = window.__visKeys ? window.__visKeys.size : null;
  return R;
}" > /tmp/lm_eval.json
python3 - <<'PY'
import json, re, sys
out = open("/tmp/lm_eval.json").read()
m = re.search(r"### Result\n(\{.*?\n\})", out, re.S)
R = json.loads(m.group(1))
fails = []
want30 = {"7455 WEST BOULEVARD", "614 E 19TH AVENUE"}
for k in ("asc", "desc", "multi"):
    if set(R["repro"][k]) != want30:
        fails.append(f"repro.{k}={R['repro'][k]}")
bands = R["bands"]
exp = {">30": 2, "20-30": 13, "10-20": 59, "0-10": 78,
       "<0": 46, "—": 26, "all": 224}
if bands != exp:
    fails.append(f"bands={bands} expected {exp}")
if sum(v for k, v in bands.items() if k != "all") != 224:
    fails.append("band total != 224")
cc = R["combo_comm"]
if cc["n"] <= 0:
    fails.append("community filter returned 0")
cd = R["combo_comm_duplex"]
if not (0 < cd["n"] < cc["n"]):
    fails.append(f"prod combo {cd['n']} !< {cc['n']}")
if R["mapKeys"] != cd["n"]:
    fails.append(f"map visKeys {R['mapKeys']} != filtered {cd['n']}")
print("FAIL " + "; ".join(fails) if fails else
      "INTERACTION PASS (sort-stable filters, bands, combo, map)")
sys.exit(1 if fails else 0)
PY
