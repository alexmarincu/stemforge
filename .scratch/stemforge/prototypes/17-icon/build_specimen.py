#!/usr/bin/env python3
"""PROTOTYPE 17 — build the icon candidate specimen.

Throwaway review aid: reads the three candidate SVGs, renders nearest-neighbour
16/20 px zooms, and writes one self-contained HTML page. Not shipped.
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

CANDIDATES = {
    "A": {
        "name": "Badge",
        "sub": "Amber disc · ink bars",
        "tag": "recommended",
        "svg": (HERE / "a-badge.svg").read_text(),
        "note": (
            "The prototype's 15 px title-bar mark, redrawn on a 256 grid: wider bars, "
            "wider gaps, and a taller rhythm so all four survive 16 px. Highest small-size "
            "legibility on both chrome themes; closest to the placeholder."
        ),
    },
    "B": {
        "name": "Ring",
        "sub": "Amber ring · amber bars · transparent",
        "tag": "",
        "svg": (HERE / "b-ring.svg").read_text(),
        "note": (
            "The airiest mark: a record-label ring with the waveform floating inside. "
            "Most distinctive at 64 px and up; at 16 px the ring breaks into a dotted "
            "circle and amber-on-white loses contrast."
        ),
    },
    "C": {
        "name": "Coin",
        "sub": "Ink disc · amber bars",
        "tag": "",
        "svg": (HERE / "c-coin.svg").read_text(),
        "note": (
            "Inverts the badge: a forged dark coin with amber bars. Strong on light chrome, "
            "quiet on dark chrome where the disc disappears and only the bars carry the "
            "mark. The mono fallback collapses to A's silhouette."
        ),
    },
}


def render(svg: str, size: int) -> bytes:
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as fh:
        fh.write(svg)
        src = fh.name
    dst = src + ".png"
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", dst, src],
            check=True,
            capture_output=True,
        )
        return Path(dst).read_bytes()
    finally:
        Path(src).unlink(missing_ok=True)
        Path(dst).unlink(missing_ok=True)


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


zooms = {
    key: {str(size): data_uri(render(c["svg"], size)) for size in (16, 20)}
    for key, c in CANDIDATES.items()
}

# Glyph study: 3, 4, 5 bars on the same amber badge.
def study_bars(n: int) -> str:
    if n == 3:
        bars = [(48, 40, 84), (108, 40, 148), (168, 40, 108)]
    elif n == 4:
        bars = [(40, 32, 76), (88, 32, 132), (136, 32, 100), (184, 32, 140)]
    else:
        bars = [
            (44, 24, 64),
            (80, 24, 112),
            (116, 24, 84),
            (152, 24, 128),
            (188, 24, 96),
        ]
    rects = "".join(
        f'<rect x="{x}" y="{128 - h // 2}" width="{w}" height="{h}" rx="{w // 2}"/>'
        for x, w, h in bars
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        '<circle cx="128" cy="128" r="116" fill="#F2A65A"/>'
        f'<g fill="#1D1204">{rects}</g></svg>'
    )


STUDY = {n: study_bars(n) for n in (3, 4, 5)}

MONO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="StemForge">
  <mask id="bars">
    <rect width="256" height="256" fill="#fff"/>
    <g fill="#000">
      <rect x="40" y="90" width="32" height="76" rx="16"/>
      <rect x="88" y="62" width="32" height="132" rx="16"/>
      <rect x="136" y="78" width="32" height="100" rx="16"/>
      <rect x="184" y="58" width="32" height="140" rx="16"/>
    </g>
  </mask>
  <circle cx="128" cy="128" r="116" fill="currentColor" mask="url(#bars)"/>
</svg>"""

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StemForge icon — candidates</title>
<style>
  :root{
    --bg:#0a0c10; --panel:#11141a; --panel2:#151920; --line:#242b36; --line2:#313b49;
    --text:#e6e9ef; --muted:#98a2b3; --dim:#6c7686; --accent:#f2a65a; --ink:#1d1204;
    --light:#f3f3f3; --light2:#e8e8e8; --light-text:#1b1e24;
    --radius:14px; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:var(--bg); color:var(--text); padding:0 0 120px;
    font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  h1,h2,h3,p{margin:0}
  .wrap{max-width:1280px;margin:0 auto;padding:0 32px}
  header.hero{padding:52px 0 26px;border-bottom:1px solid var(--line)}
  .eyebrow{font:11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}
  h1{font-size:clamp(26px,3vw,38px);line-height:1.1;letter-spacing:-.02em;font-weight:680}
  .lede{max-width:820px;margin-top:14px;color:var(--muted);font-size:15px}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}
  .chip{font:11px/1 var(--mono);color:var(--muted);border:1px solid var(--line2);border-radius:999px;padding:7px 11px}
  .chip b{color:var(--text);font-weight:600}
  .swatch{display:inline-block;width:9px;height:9px;border-radius:2px;vertical-align:-1px;margin-right:5px}
  main{padding-top:26px}
  .rec{
    display:flex;gap:14px;align-items:flex-start;background:rgba(242,166,90,.07);
    border:1px solid rgba(242,166,90,.28);border-radius:12px;padding:14px 16px;margin-bottom:22px;
  }
  .rec .ic{color:var(--accent);flex:none;margin-top:1px}
  .rec b{color:var(--accent)}
  .rec p{color:var(--muted);font-size:13px}
  .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
  @media (max-width:1120px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;display:flex;flex-direction:column}
  .card-head{display:flex;align-items:flex-start;gap:12px;padding:18px 18px 14px;border-bottom:1px solid var(--line)}
  .letter{
    width:28px;height:28px;border-radius:8px;background:var(--panel2);border:1px solid var(--line2);
    display:grid;place-items:center;font:600 13px/1 var(--mono);color:var(--accent);flex:none;
  }
  .card-head h2{font-size:16px;font-weight:650;line-height:1.2}
  .card-head p{color:var(--muted);font-size:12px;margin-top:2px}
  .tag{margin-left:auto;font:10px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--accent);border:1px solid rgba(242,166,90,.4);background:rgba(242,166,90,.08);border-radius:999px;padding:5px 8px;white-space:nowrap}
  .split{display:grid;grid-template-columns:1fr 1fr;height:196px;border-bottom:1px solid var(--line)}
  .half{display:grid;place-items:center;position:relative}
  .half.dark{background:#0e1014}
  .half.light{background:var(--light)}
  .half span{position:absolute;bottom:8px;font:9px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;opacity:.55}
  .half.dark span{color:var(--muted)}
  .half.light span{color:#5d6572}
  .body{padding:16px 18px 18px;display:flex;flex-direction:column;gap:16px}
  .ladder{display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px 16px;min-height:70px}
  .sz{display:flex;flex-direction:column;align-items:center;gap:5px;margin:0}
  .sz .art{height:64px;display:flex;align-items:flex-end}
  .sz figcaption{font:9px/1 var(--mono);color:var(--dim)}
  .zooms{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
  .zoom{display:flex;flex-direction:column;gap:5px;align-items:center}
  .zoom .box{width:100%;height:100px;border-radius:8px;border:1px solid var(--line2);display:grid;place-items:center;overflow:hidden}
  .zoom .box.dark{background:#0e1014}
  .zoom .box.light{background:var(--light);border-color:#d8d8d8}
  .zoom img{image-rendering:pixelated}
  .zoom img.z16{width:64px;height:64px}
  .zoom img.z20{width:80px;height:80px}
  .zoom small{font:9px/1.2 var(--mono);color:var(--dim);text-align:center}
  .contexts{display:flex;flex-direction:column;gap:8px}
  .ctx-label{font:9px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--dim)}
  .tb{height:30px;border-radius:6px;display:flex;align-items:center;gap:7px;padding:0 8px;font-size:11px;overflow:hidden}
  .tb.light{background:var(--light);color:var(--light-text);border:1px solid #d6d6d6}
  .tb.dark{background:#202020;color:#ececec;border:1px solid #333}
  .tb .dots{margin-left:auto;display:flex;gap:8px;opacity:.6;font-size:10px}
  .tb .app{width:16px;height:16px;flex:none;display:grid;place-items:center}
  .task{height:42px;border-radius:6px;display:flex;align-items:center;gap:8px;padding:0 8px}
  .task.light{background:var(--light);border:1px solid #d6d6d6}
  .task.dark{background:#202020;border:1px solid #333}
  .task .fake{width:26px;height:26px;border-radius:6px;background:#c9ced8;opacity:.55;flex:none}
  .task.dark .fake{background:#4a5361}
  .task .app{width:32px;height:32px;display:grid;place-items:center;flex:none}
  .task .sep{width:1px;height:22px;background:#d0d0d0;margin-left:auto}
  .task.dark .sep{background:#3a3a3a}
  .note{color:var(--muted);font-size:12.5px;line-height:1.5;border-top:1px solid var(--line);padding-top:13px}
  .note b{color:var(--text);font-weight:600}
  .wide{grid-column:1/-1}
  .wide .split{height:300px}
  .wide .body{display:grid;grid-template-columns:1fr 1fr;gap:20px 26px}
  .wide .ladder,.wide .zooms,.wide .contexts,.wide .note{grid-column:auto}
  .wide .ladder{grid-column:1/-1}
  .wide .sz .art{height:256px}
  .wide .note{grid-column:1/-1}
  @media (max-width:900px){.wide .body{display:flex}}
  section.shared{margin-top:38px}
  .sec-head{display:flex;align-items:baseline;gap:12px;margin-bottom:14px}
  .sec-head h2{font-size:18px;font-weight:650}
  .sec-head p{color:var(--muted);font-size:12.5px}
  .shared-grid{display:grid;grid-template-columns:1.25fr 1fr;gap:20px}
  @media (max-width:1000px){.shared-grid{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px}
  .panel h3{font-size:14px;font-weight:650;margin-bottom:4px}
  .panel .sub{color:var(--muted);font-size:12px;margin-bottom:14px}
  .study-row{display:flex;align-items:center;gap:14px;padding:10px 0;border-top:1px solid var(--line)}
  .study-row:first-of-type{border-top:0}
  .study-row .bars{width:74px;font:11px/1 var(--mono);color:var(--muted);flex:none}
  .study-row .set{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap}
  .study-row .set span{display:flex;flex-direction:column;align-items:center;gap:4px}
  .study-row .set i{font:9px/1 var(--mono);color:var(--dim);font-style:normal}
  .study-row .verdict{font-size:11px;color:var(--accent);margin-left:auto;text-align:right}
  .study-row.win{background:rgba(242,166,90,.06);margin:0 -10px;padding:10px;border-radius:8px;border-top-color:transparent}
  .mono-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:4px}
  .mono-box{border-radius:9px;padding:10px 6px;display:flex;flex-direction:column;align-items:center;gap:6px;border:1px solid var(--line)}
  .mono-box .row{display:flex;align-items:flex-end;gap:10px}
  .mono-box small{font:9px/1 var(--mono);color:var(--dim)}
  .mono-box.on-dark{background:#0e1014;color:#e6e9ef}
  .mono-box.on-light{background:var(--light);border-color:#d8d8d8;color:#1d1204}
  .mono-box.on-blue{background:#0b57d0;color:#fff}
  .fa{font-family:var(--mono);font-size:11.5px;color:var(--muted);background:#0c0f14;border:1px solid var(--line);border-radius:8px;padding:12px 13px;overflow:auto;line-height:1.6}
  .fa b{color:var(--accent);font-weight:500}
  .panel p.copy{color:var(--muted);font-size:12.5px;margin-top:10px}
  .panel p.copy b{color:var(--text);font-weight:600}
  footer{margin-top:34px;padding-top:18px;border-top:1px solid var(--line);color:var(--dim);font-size:11px}
  #bar{
    position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:20;
    display:flex;align-items:center;gap:6px;background:rgba(17,20,26,.94);border:1px solid var(--line2);
    border-radius:999px;padding:6px;box-shadow:0 14px 40px rgba(0,0,0,.5);backdrop-filter:blur(12px);
  }
  #bar .vtag{font:9px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--dim);padding:0 8px}
  #bar button{font:12px/1 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--muted);background:none;border:0;border-radius:999px;padding:9px 12px;cursor:pointer;white-space:nowrap}
  #bar button:hover{color:var(--text);background:var(--panel2)}
  #bar button.on{color:#201404;background:var(--accent);font-weight:650}
  #bar button .k{font:9px/1 var(--mono);opacity:.55;margin-left:5px}
  #bar button.on .k{opacity:.7}
  @media (max-width:700px){#bar .vtag,#bar button .k{display:none}#bar button{padding:9px 10px}}
  @media (prefers-reduced-motion:no-preference){.card{transition:border-color .15s ease}.card:hover{border-color:var(--line2)}}
</style>
</head>
<body>
<header class="hero">
  <div class="wrap">
    <div class="eyebrow">StemForge · wayfinder 17 · icon candidates</div>
    <h1>Three ways to forge the mark</h1>
    <p class="lede">
      The contract is fixed: one master SVG, a Windows ICO from 16 to 256 px, and 512/1024 PNGs.
      The open question is the mark. Sizes below are <b>actual pixels</b>; the zoomed rows are
      nearest-neighbour 16/20 px renders, so you can judge the pixel truth rather than a smooth preview.
    </p>
    <div class="chips">
      <span class="chip"><span class="swatch" style="background:#F2A65A"></span><b>#F2A65A</b> amber, from ticket 08</span>
      <span class="chip"><span class="swatch" style="background:#1D1204"></span><b>#1D1204</b> ink, from the prototype</span>
      <span class="chip"><b>4 bars</b> = four Demucs stems</span>
      <span class="chip">transparent field · no baked background</span>
      <span class="chip"><b>16 px first</b>, then 256</span>
    </div>
  </div>
</header>

<main class="wrap">
  <div class="rec">
    <svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l9 16H3z"/><path d="M12 9v4m0 3h.01"/></svg>
    <div>
      <p><b>Designer's recommendation: A — Badge.</b> It is the only candidate that keeps four
      distinct bars at 16 px on both light and dark chrome, and it is the mark the UI prototype
      already uses at 15 px. B is the most elegant at 64 px and up; C is the strongest on light
      chrome. The human still chooses.</p>
    </div>
  </div>
  <div id="view"></div>

  <section class="shared">
    <div class="sec-head">
      <h2>Shared decisions</h2>
      <p>These follow whichever mark wins.</p>
    </div>
    <div class="shared-grid">
      <div class="panel">
        <h3>Glyph study — 4 bars wins</h3>
        <p class="sub">Same amber badge, same 256 grid, varying bar count. Actual size; 16 px is the test.</p>
        <div class="study-row">
          <div class="bars">3 bars</div>
          <div class="set" data-study="3"></div>
          <div class="verdict">loses the waveform rhythm</div>
        </div>
        <div class="study-row win">
          <div class="bars">4 bars</div>
          <div class="set" data-study="4"></div>
          <div class="verdict">four stems, still four slots at 16 px</div>
        </div>
        <div class="study-row">
          <div class="bars">5 bars</div>
          <div class="set" data-study="5"></div>
          <div class="verdict">picket fence at 16 px</div>
        </div>
      </div>

      <div class="panel">
        <h3>Monochrome fallback</h3>
        <p class="sub">One file, <span class="fa" style="padding:1px 4px">currentColor</span>, bars knocked out as holes. For trays, forced-colour themes and high-contrast mode.</p>
        <div class="mono-grid" id="mono-grid"></div>
        <p class="copy"><b>Why holes, not a second colour:</b> a single-colour render of a filled badge would merge disc and bars into a blob. The mask keeps the waveform visible at any tint.</p>
      </div>

      <div class="panel wide">
        <h3>AppImage <span class="fa" style="padding:1px 4px">.DirIcon</span> treatment</h3>
        <p class="sub">A symlink to the scalable icon, not a fourth raster asset.</p>
        <pre class="fa">StemForge.AppDir/
├── <b>.DirIcon</b> → usr/share/icons/hicolor/scalable/apps/stemforge.svg
├── stemforge.desktop
└── usr/share/icons/hicolor/
    ├── scalable/apps/stemforge.svg
    └── 512x512/apps/stemforge-512.png</pre>
        <p class="copy"><b>Why:</b> the AppImage spec accepts SVG for <span class="fa" style="padding:1px 4px">.DirIcon</span>; the same master feeds the scalable icon, the 512 px raster and the About modal. No third copy to drift. If a future appimagetool rejects SVG, packaging falls back to the 512 px PNG.</p>
      </div>
    </div>
  </section>

  <footer>Prototype 17 · throwaway review aid, not production. Editable candidates: a-badge.svg, b-ring.svg, c-coin.svg. Choose A, B or C in chat.</footer>
</main>

<div id="bar">
  <span class="vtag">mark</span>
  <button data-view="all">All <span class="k">0</span></button>
  <button data-view="A">A · Badge <span class="k">1</span></button>
  <button data-view="B">B · Ring <span class="k">2</span></button>
  <button data-view="C">C · Coin <span class="k">3</span></button>
</div>

<script>
const MARKS = __MARKS__;
const ZOOMS = __ZOOMS__;
const STUDY = __STUDY__;
const MONO = __MONO__;

function at(key, size) {
  return MARKS[key].svg.replace('<svg ', `<svg width="${size}" height="${size}" `);
}
function studyAt(n, size) {
  return STUDY[n].replace('<svg ', `<svg width="${size}" height="${size}" `);
}
function monoAt(size, color) {
  return MONO.replace('<svg ', `<svg width="${size}" height="${size}" style="color:${color}" `);
}
function ladder(key, sizes) {
  return `<div class="ladder">` + sizes.map(s => `
    <figure class="sz"><div class="art">${at(key, s)}</div><figcaption>${s}</figcaption></figure>`).join('') + `</div>`;
}
function zooms(key) {
  return `<div class="zooms">
    <div class="zoom"><div class="box dark">${zoomImg(key, 16)}</div><small>16 px · dark</small></div>
    <div class="zoom"><div class="box light">${zoomImg(key, 16)}</div><small>16 px · light</small></div>
    <div class="zoom"><div class="box dark">${zoomImg(key, 20)}</div><small>20 px · dark</small></div>
    <div class="zoom"><div class="box light">${zoomImg(key, 20)}</div><small>20 px · light</small></div>
  </div>`;
}
function zoomImg(key, size) {
  return `<img class="z${size}" src="${ZOOMS[key][String(size)]}" width="${size}" height="${size}" alt="${key} at ${size} px">`;
}
function contexts(key) {
  return `<div class="contexts">
    <div class="ctx-label">Windows title bar</div>
    <div class="tb light"><span class="app">${at(key, 16)}</span><span>StemForge — Midnight Drive</span><span class="dots"><i>—</i><i>▢</i><i>✕</i></span></div>
    <div class="tb dark"><span class="app">${at(key, 16)}</span><span>StemForge — Midnight Drive</span><span class="dots"><i>—</i><i>▢</i><i>✕</i></span></div>
    <div class="ctx-label" style="margin-top:4px">Windows taskbar</div>
    <div class="task light"><span class="fake"></span><span class="app">${at(key, 32)}</span><span class="fake"></span><span class="sep"></span></div>
    <div class="task dark"><span class="fake"></span><span class="app">${at(key, 32)}</span><span class="fake"></span><span class="sep"></span></div>
  </div>`;
}
function card(key, wide) {
  const c = MARKS[key];
  return `<article class="card${wide ? ' wide' : ''}">
    <div class="card-head">
      <span class="letter">${key}</span>
      <div><h2>${c.name}</h2><p>${c.sub}</p></div>
      ${c.tag ? `<span class="tag">${c.tag}</span>` : ''}
    </div>
    <div class="split">
      <div class="half dark">${at(key, wide ? 220 : 132)}<span>dark chrome</span></div>
      <div class="half light">${at(key, wide ? 220 : 132)}<span>light chrome</span></div>
    </div>
    <div class="body">
      ${ladder(key, wide ? [16,20,24,32,40,48,64,128,256] : [16,20,24,32,48,64])}
      ${zooms(key)}
      ${contexts(key)}
      <p class="note">${c.note}</p>
    </div>
  </article>`;
}
function render() {
  const view = getView();
  const el = document.getElementById('view');
  el.className = view === 'all' ? 'grid' : '';
  if (view === 'all') {
    el.innerHTML = ['A','B','C'].map(k => card(k, false)).join('');
  } else {
    el.innerHTML = card(view, true);
  }
  document.querySelectorAll('#bar button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
  document.querySelectorAll('[data-study]').forEach(node => {
    const n = node.dataset.study;
    node.innerHTML = [16,24,32,64].map(s => `<span>${studyAt(n, s)}<i>${s}</i></span>`).join('');
  });
  const mg = document.getElementById('mono-grid');
  const boxes = [
    ['on-dark','on dark','#e6e9ef'],
    ['on-light','on light','#1d1204'],
    ['on-blue','forced blue','#ffffff'],
  ];
  mg.innerHTML = boxes.map(([cls,label,color]) => `
    <div class="mono-box ${cls}"><div class="row">${[16,24,32].map(s => monoAt(s, color)).join('')}</div><small>${label}</small></div>`).join('');
  document.title = view === 'all' ? 'StemForge icon — candidates' : `StemForge icon — ${MARKS[view].name}`;
}
function getView() {
  const p = new URLSearchParams(location.search).get('candidate');
  return p && MARKS[p.toUpperCase()] ? p.toUpperCase() : 'all';
}
function setView(v) {
  if (v === 'all') history.replaceState(null, '', location.pathname);
  else history.replaceState(null, '', '?candidate=' + v);
  render();
}
document.querySelectorAll('#bar button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
addEventListener('keydown', e => {
  const map = {'0':'all','1':'A','2':'B','3':'C'};
  if (!e.metaKey && !e.ctrlKey && !e.altKey && map[e.key]) { setView(map[e.key]); }
});
render();
</script>
</body>
</html>
"""

html = (
    HTML.replace("__MARKS__", json.dumps(CANDIDATES))
    .replace("__ZOOMS__", json.dumps(zooms))
    .replace("__STUDY__", json.dumps(STUDY))
    .replace("__MONO__", json.dumps(MONO))
)
out = HERE / "17-icon-candidates.html"
out.write_text(html)
print(f"wrote {out} ({len(html):,} bytes)")
