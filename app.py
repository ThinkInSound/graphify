"""
Graphify Desktop App
====================
Drop a folder onto the window to generate an interactive knowledge graph.

Requirements:
    pip install customtkinter pyvis tkinterdnd2
"""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

# ── Optional drag-and-drop ───────────────────────────────────────────────────
try:
    import tkinterdnd2 as TkinterDnD  # type: ignore
    _DND = True
except ImportError:
    _DND = False

import networkx as nx

# ─────────────────────────────────────────────────────────────────────────────
# Cohere-inspired palette  (from DESIGN.md)
# ─────────────────────────────────────────────────────────────────────────────

C_WHITE      = "#ffffff"      # Pure White — primary surface
C_SNOW       = "#fafafa"      # Snow — elevated surface
C_BLACK      = "#000000"      # Cohere Black — primary text
C_NEAR_BLK   = "#212121"      # Near Black — body text
C_MUTED      = "#93939f"      # Muted Slate — de-emphasised text
C_BORDER_LT  = "#f2f2f2"      # Lightest Gray — subtle card borders
C_BORDER_MID = "#d9d9dd"      # Border Cool — standard section borders
C_BLUE       = "#1863dc"      # Interaction Blue — hover / focus only
C_PURPLE_HDR = "#1e0a3c"      # Deep purple — header hero band
C_PURPLE_SUB = "#2d1247"      # Mid purple — header subtitle row

# ─────────────────────────────────────────────────────────────────────────────
# Processing constants
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_TEXT_EXT = {".txt", ".md", ".rst", ".markdown", ".text"}
CODE_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".h", ".go", ".rs", ".rb"}

# ─────────────────────────────────────────────────────────────────────────────
# Processing logic
# ─────────────────────────────────────────────────────────────────────────────

def _collect_files(folder: str, recurse: bool, include_code: bool):
    exts = SUPPORTED_TEXT_EXT | (CODE_EXT if include_code else set())
    root = Path(folder)
    if recurse:
        return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts)
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _parse_markdown_sections(filepath: Path, include_text: bool):
    """Parse markdown headings. Returns [{label, level, text}]."""
    try:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    sections, cur_heading, cur_text = [], None, []
    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if cur_heading:
                sections.append({
                    "label": cur_heading["label"],
                    "level": cur_heading["level"],
                    "text":  "\n".join(cur_text[:6]) if include_text else "",
                })
            cur_heading = {"label": m.group(2).strip(), "level": len(m.group(1))}
            cur_text = []
        elif line.strip() and cur_heading:
            cur_text.append(line.strip())

    if cur_heading:
        sections.append({
            "label": cur_heading["label"],
            "level": cur_heading["level"],
            "text":  "\n".join(cur_text[:6]) if include_text else "",
        })
    return sections


def _find_keyword_links(files, threshold: int = 4):
    """Find file pairs sharing ≥ threshold keywords."""
    STOP = {
        "the","a","an","is","in","it","of","to","and","or","for","on","at","by",
        "be","as","are","was","were","with","that","this","from","they","have",
        "had","not","but","you","your","our","their","its","will","can","may",
        "has","we","he","she","if","do","so","more","use","used","also",
    }
    kw: dict = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            kw[str(f)] = set(re.findall(r"\b[a-z]{4,}\b", text)) - STOP
        except Exception:
            kw[str(f)] = set()

    keys = list(kw)
    edges = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            shared = len(kw[keys[i]] & kw[keys[j]])
            if shared >= threshold:
                edges.append((keys[i], keys[j], shared))
    return edges


CONTENT_LIMIT = 6000   # chars stored per node for the detail panel


def build_graph(folder: str, output: str, options: dict, progress_cb, status_cb) -> str:
    """Build graph and render to HTML. Returns output file path."""
    folder_path = Path(folder)
    output_dir  = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    status_cb("Scanning folder…")
    progress_cb(0.04)

    files = _collect_files(folder, options["recurse"], options["include_code"])
    if not files:
        raise ValueError(f"No supported files found in:\n{folder}")

    status_cb(f"Found {len(files)} file(s) — building graph…")
    progress_cb(0.10)

    G = nx.DiGraph()
    root_id = "__ROOT__"
    G.add_node(root_id, label=folder_path.name, ntype="folder",
               content="", fpath=str(folder_path))

    file_node_map: dict = {}

    for idx, filepath in enumerate(files):
        progress_cb(0.10 + 0.55 * (idx / len(files)))
        status_cb(f"Processing  {filepath.name}")

        rel   = filepath.relative_to(folder_path)
        fid   = f"FILE::{rel}"
        ext   = filepath.suffix.lower()
        ntype = "code" if ext in CODE_EXT else "document"
        kb    = filepath.stat().st_size / 1024

        # Read full content for detail panel
        content = ""
        readable = SUPPORTED_TEXT_EXT | CODE_EXT
        if ext in readable:
            try:
                raw = filepath.read_text(encoding="utf-8", errors="ignore")
                content = raw[:CONTENT_LIMIT]
                if len(raw) > CONTENT_LIMIT:
                    content += f"\n\n… ({len(raw) - CONTENT_LIMIT:,} more characters)"
            except Exception:
                pass

        parent_id = root_id
        parts = rel.parts
        for depth in range(len(parts) - 1):
            dir_id = "DIR::" + "/".join(parts[: depth + 1])
            if dir_id not in G:
                G.add_node(dir_id, label=parts[depth], ntype="directory",
                           content="", fpath="")
                G.add_edge(parent_id, dir_id)
            parent_id = dir_id

        G.add_node(fid, label=filepath.name, ntype=ntype,
                   fpath=str(filepath), content=content,
                   meta=f"{ext[1:].upper() or 'FILE'}  ·  {kb:.1f} KB")
        G.add_edge(parent_id, fid)
        file_node_map[str(filepath)] = fid

        if options["parse_structure"] and ext in SUPPORTED_TEXT_EXT:
            sections    = _parse_markdown_sections(filepath, options["include_text"])
            level_stack = {0: fid}
            for s_idx, sec in enumerate(sections):
                sid = f"{fid}::S{s_idx}"
                G.add_node(sid, label=sec["label"], ntype="section",
                           depth=sec["level"], content=sec["text"],
                           fpath="", meta=f"H{sec['level']} section")
                pl = sec["level"] - 1
                while pl > 0 and pl not in level_stack:
                    pl -= 1
                G.add_edge(level_stack.get(pl, fid), sid)
                level_stack[sec["level"]] = sid

    # Detect [[wikilinks]] and create edges
    status_cb("Detecting wikilinks…")
    progress_cb(0.68)
    name_map: dict = {}
    for nid, data in G.nodes(data=True):
        fp = data.get("fpath", "")
        if fp and data.get("ntype") in ("document", "code"):
            p = Path(fp)
            name_map[p.stem.lower()]  = nid
            name_map[p.name.lower()]  = nid
    for nid, data in G.nodes(data=True):
        for m in re.finditer(r"\[\[([^\]|#\n]+)\]\]", data.get("content", "")):
            target = name_map.get(m.group(1).strip().lower())
            if target and target != nid and not G.has_edge(nid, target):
                G.add_edge(nid, target, etype="wikilink",
                           title=f"[[{m.group(1)}]]")

    if options["link_related"]:
        status_cb("Finding related content…")
        progress_cb(0.76)
        text_files = [f for f in files if f.suffix.lower() in SUPPORTED_TEXT_EXT]
        if len(text_files) > 1:
            for pa, pb, shared in _find_keyword_links(text_files):
                na, nb = file_node_map.get(pa), file_node_map.get(pb)
                if na and nb and not G.has_edge(na, nb):
                    G.add_edge(na, nb, etype="related", weight=shared,
                               title=f"{shared} shared keywords")

    status_cb("Rendering graph…")
    progress_cb(0.86)

    safe_name = re.sub(r"[^\w\-]", "_", folder_path.name)
    html_out  = str(output_dir / f"{safe_name}_graph.html")
    _render_html(G, html_out, folder_path.name)

    if options.get("export_json"):
        json_out = str(output_dir / f"{safe_name}_graph.json")
        Path(json_out).write_text(
            json.dumps(nx.node_link_data(G), indent=2, default=str),
            encoding="utf-8",
        )

    progress_cb(1.0)
    status_cb("Done!")
    return html_out


# ── Rendering ─────────────────────────────────────────────────────────────────

_NODE_STYLE = {
    "folder":    {"color": "#e8b86d", "size": 28},
    "directory": {"color": "#b8a4d4", "size": 20},
    "document":  {"color": "#7db8d4", "size": 16},
    "code":      {"color": "#8dc4a0", "size": 16},
    "section":   {"color": "#c4b0a4", "size": 9},
}


def _render_html(G: nx.DiGraph, out_path: str, title: str):
    """Render the full three-panel SPA (sidebar · graph · detail)."""
    nodes_data = []
    for nid, data in G.nodes(data=True):
        style = _NODE_STYLE.get(data.get("ntype", "document"), _NODE_STYLE["document"])
        depth = data.get("depth", 0)
        size  = style["size"] if data.get("ntype") != "section" else max(5, 12 - depth * 2)
        nodes_data.append({
            "id":          nid,
            "label":       data.get("label", nid),
            "ntype":       data.get("ntype", "document"),
            "content":     data.get("content", ""),
            "fpath":       data.get("fpath", ""),
            "meta":        data.get("meta", ""),
            "depth":       depth,
            "connections": G.degree(nid),
            "color":       style["color"],
            "size":        size,
        })

    edges_data = []
    for src, dst, data in G.edges(data=True):
        edges_data.append({
            "from":  src,
            "to":    dst,
            "etype": data.get("etype", "default"),
            "weight": data.get("weight", 1),
            "title": data.get("title", ""),
        })

    graph_json = json.dumps({"nodes": nodes_data, "edges": edges_data},
                            ensure_ascii=False)
    # Prevent </script> injection from file content
    graph_json = graph_json.replace("</", "<\\/")

    html = _HTML_TEMPLATE
    html = html.replace("__TITLE__",   title)
    html = html.replace("__N_NODES__", str(G.number_of_nodes()))
    html = html.replace("__N_EDGES__", str(G.number_of_edges()))
    html = html.replace("__GRAPH_JSON__", graph_json)
    Path(out_path).write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML template — three-panel SPA
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — Graphify</title>
<style>
:root {
  --gbg:  #16120e;
  --ui:   #f9f7f4;
  --mid:  #ede8e2;
  --low:  #d9d2c8;
  --acc:  #c4a87a;
  --txt:  #2a241f;
  --mut:  #9a8e84;
  --hdr:  #1e1916;
  --hh:   44px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;background:var(--gbg);
     height:100vh;display:flex;flex-direction:column;overflow:hidden;
     color:var(--txt)}

/* ── header ─────────────────────────────────────────────────────────────── */
#hdr{height:var(--hh);background:var(--hdr);display:flex;align-items:center;
     padding:0 16px;gap:12px;flex-shrink:0}
#ham{background:none;border:none;color:rgba(255,255,255,.45);cursor:pointer;
     font-size:16px;padding:4px 5px;line-height:1;flex-shrink:0}
#ham:hover{color:rgba(255,255,255,.9)}
.hlogo{font-size:13px;font-weight:600;color:rgba(255,255,255,.85);
       letter-spacing:.2px;white-space:nowrap}
.hsep{width:1px;height:16px;background:rgba(255,255,255,.12);flex-shrink:0}
.htitle{color:rgba(255,255,255,.4);font-size:12px;flex:1;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
.hstats{font-size:11px;color:rgba(255,255,255,.22);white-space:nowrap;
        letter-spacing:.1px}

/* ── layout ─────────────────────────────────────────────────────────────── */
#layout{display:flex;flex:1;overflow:hidden;position:relative}

/* ── sidebar ────────────────────────────────────────────────────────────── */
#sb{width:252px;background:var(--ui);border-right:1px solid var(--low);
    display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
#sb-top{padding:10px 12px 8px;border-bottom:1px solid var(--low);flex-shrink:0}
#search{width:100%;padding:6px 10px;border:1px solid var(--low);border-radius:6px;
        font-size:12px;font-family:inherit;color:var(--txt);background:var(--mid);
        outline:none;transition:border-color .15s}
#search:focus{border-color:var(--acc)}
#search::placeholder{color:var(--mut)}
#sortbar{display:flex;gap:4px;margin-top:7px}
.sbtn{flex:1;padding:3px 0;border:1px solid var(--low);border-radius:4px;
      font-size:10px;font-family:inherit;letter-spacing:.2px;text-transform:uppercase;
      color:var(--mut);background:transparent;cursor:pointer;transition:all .15s}
.sbtn:hover,.sbtn.on{border-color:var(--acc);color:var(--acc)}
#nlist{flex:1;overflow-y:auto;padding:4px 0}

/* node groups */
.grp-hdr{display:flex;align-items:center;gap:6px;padding:6px 12px 4px;
          cursor:pointer;user-select:none;font-size:10px;letter-spacing:.25px;
          text-transform:uppercase;color:var(--mut)}
.grp-hdr:hover{color:var(--txt)}
.garrow{font-size:7px;transition:transform .18s;margin-left:auto;opacity:.5}
.grp.closed .garrow{transform:rotate(-90deg)}
.grp.closed .grp-items{display:none}
.gcnt{background:var(--mid);color:var(--mut);border-radius:9999px;
      padding:1px 6px;font-size:9px}
.ni{display:flex;align-items:center;gap:8px;padding:5px 12px;cursor:pointer;
    transition:background .1s;border-left:2px solid transparent}
.ni:hover{background:var(--mid)}
.ni.on{background:var(--mid);border-left-color:var(--acc)}
.ni.dim{opacity:.2}
.ndot{width:7px;height:7px;border-radius:50%;flex-shrink:0;opacity:.85}
.nlbl{font-size:12px;color:var(--txt);overflow:hidden;text-overflow:ellipsis;
      white-space:nowrap;flex:1}
.nct{font-size:10px;color:var(--mut);white-space:nowrap}

/* ── graph ──────────────────────────────────────────────────────────────── */
#gw{flex:1;position:relative;background:var(--gbg);overflow:hidden;min-width:0}
#gc{width:100%;height:100%}
#ghost{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);
       color:rgba(255,255,255,.15);font-size:10px;letter-spacing:.15px;
       pointer-events:none;white-space:nowrap}

/* stabilising overlay */
#stabilizing{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
              background:rgba(22,18,14,.88);color:rgba(196,168,122,.9);
              padding:20px 26px;border-radius:10px;text-align:center;
              font-size:11px;letter-spacing:.15px;pointer-events:none;
              min-width:210px;border:1px solid rgba(196,168,122,.15)}
#stab-txt{display:block;margin-bottom:11px;font-size:11px}
#stab-track{background:rgba(255,255,255,.08);border-radius:99px;height:3px;margin-bottom:7px}
#stab-fill{background:var(--acc);border-radius:99px;height:3px;width:0%;
           transition:width .12s linear}
#stab-pct{color:rgba(196,168,122,.45);font-size:10px}

/* ── detail panel — fixed overlay, never affects graph width ────────────── */
#dp{position:fixed;right:0;top:var(--hh);bottom:0;width:340px;
    background:var(--ui);border-left:1px solid var(--low);
    display:flex;flex-direction:column;overflow:hidden;
    transform:translateX(100%);z-index:100}
#dp-resize{position:absolute;left:0;top:0;bottom:0;width:5px;
           cursor:ew-resize;z-index:10;background:transparent}
#dp-resize:hover{background:rgba(196,168,122,.3)}
#dp-hdr{padding:14px 16px 10px;border-bottom:1px solid var(--low);flex-shrink:0}
#dp-close{float:right;background:none;border:none;font-size:16px;cursor:pointer;
           color:var(--mut);line-height:1;padding:0}
#dp-close:hover{color:var(--txt)}
#dp-icon{font-size:18px;display:block;margin-bottom:4px}
#dp-title{font-size:14px;font-weight:600;color:var(--txt);
           word-break:break-word;margin-right:20px;line-height:1.3}
#dp-badge{display:inline-block;margin-top:5px;padding:2px 8px;border-radius:4px;
           font-size:9px;letter-spacing:.3px;text-transform:uppercase;
           background:var(--mid);color:var(--mut)}
#dp-body{flex:1;overflow-y:auto;padding:12px 16px}
.ds{margin-bottom:16px}
.ds-lbl{font-size:9px;letter-spacing:.3px;text-transform:uppercase;
         color:var(--mut);margin-bottom:6px}
.dmr{display:flex;gap:6px;align-items:baseline;margin-bottom:3px;font-size:11px}
.dmk{color:var(--mut);min-width:48px;flex-shrink:0}
.dmv{color:var(--txt);word-break:break-all;font-family:"Courier New",monospace;font-size:10px}
.cl{display:flex;align-items:center;gap:7px;padding:4px 8px;border-radius:6px;
    cursor:pointer;margin-bottom:2px;transition:background .1s}
.cl:hover{background:var(--mid)}
.cl .cd{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.cl .cn{font-size:11px;color:var(--txt);flex:1;overflow:hidden;
         text-overflow:ellipsis;white-space:nowrap}
.cl .ct{font-size:9px;color:var(--mut)}
.cl:hover .cn{color:var(--acc)}
#dp-content{font-size:11px;line-height:1.7;color:var(--txt);
             white-space:pre-wrap;word-break:break-word;background:var(--mid);
             border-radius:6px;padding:11px;
             max-height:300px;overflow-y:auto;tab-size:2}
.wl{color:var(--acc);cursor:pointer;text-decoration:underline;
    text-underline-offset:2px}
.wl:hover{opacity:.7}
.wl-dead{color:var(--mut);text-decoration:underline dashed;
          text-underline-offset:2px;cursor:default}

/* scrollbars */
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--low);border-radius:99px}
</style>
</head>
<body>

<div id="hdr">
  <button id="ham" title="Toggle sidebar">&#9776;</button>
  <span class="hlogo">&#10021; Graphify</span>
  <div class="hsep"></div>
  <span class="htitle">__TITLE__</span>
  <span class="hstats">__N_NODES__ &middot; __N_EDGES__</span>
</div>

<div id="layout">
  <nav id="sb">
    <div id="sb-top">
      <input id="search" type="text" placeholder="Search&hellip;" autocomplete="off">
      <div id="sortbar">
        <button class="sbtn on" data-s="az">A&ndash;Z</button>
        <button class="sbtn" data-s="za">Z&ndash;A</button>
        <button class="sbtn" data-s="links">Links</button>
      </div>
    </div>
    <div id="nlist"></div>
  </nav>

  <div id="gw">
    <div id="gc"></div>
    <div id="stabilizing">
      <span id="stab-txt">Laying out graph&hellip;</span>
      <div id="stab-track"><div id="stab-fill"></div></div>
      <span id="stab-pct">0 / __N_NODES__ nodes</span>
    </div>
    <div id="ghost">click node &nbsp;&middot;&nbsp; scroll zoom &nbsp;&middot;&nbsp; drag pan</div>
  </div>

  <aside id="dp">
    <div id="dp-resize"></div>
    <div id="dp-hdr">
      <button id="dp-close" title="Close">&times;</button>
      <span id="dp-icon">&#128196;</span>
      <div id="dp-title">&mdash;</div>
      <span id="dp-badge">&mdash;</span>
    </div>
    <div id="dp-body">
      <div class="ds" id="s-meta"><div class="ds-lbl">Metadata</div><div id="dp-meta"></div></div>
      <div class="ds" id="s-conn"><div class="ds-lbl">Connections</div><div id="dp-conn"></div></div>
      <div class="ds" id="s-cont"><div class="ds-lbl">Content</div><div id="dp-content"></div></div>
    </div>
  </aside>
</div>

<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>
<script>
const G = __GRAPH_JSON__;
const NM = Object.fromEntries(G.nodes.map(n => [n.id, n]));
const COLORS = {folder:'#f59e0b',directory:'#a78bfa',document:'#38bdf8',
                code:'#34d399',section:'#818cf8'};
const ICONS  = {folder:'&#128193;',directory:'&#128194;',document:'&#128196;',
                code:'&#128187;',section:'&#35;'};

// ── vis.js dataset ────────────────────────────────────────────────────────────
const VN = new vis.DataSet(G.nodes.map(n => ({
  id: n.id,
  label: n.ntype === 'section' ? '' : n.label,
  title: n.label,
  color:{background:n.color,border:n.color,
         highlight:{background:'#fff',border:n.color},
         hover:{background:n.color,border:'#fff'}},
  shape:'dot', size:n.size,
  font:{size:11,color:'#8892a4',face:'Inter,Segoe UI,sans-serif'},
})));

const VE = new vis.DataSet(G.edges.map((e,i) => ({
  id:i, from:e.from, to:e.to, arrows:'to',
  color:{color: e.etype==='wikilink'?'#7c3aed':
                e.etype==='related' ?'#4f46e5':'#1e293b',
         highlight:'#3b82f6',hover:'#3b82f6'},
  width: e.etype==='wikilink'?2:1,
  dashes: e.etype==='related',
  title: e.title||'',
  smooth:{type:'continuous'},
})));

const gc  = document.getElementById('gc');
const gw  = document.getElementById('gw');
const net = new vis.Network(gc, {nodes:VN, edges:VE}, {
  physics:{
    solver:'barnesHut',
    barnesHut:{gravitationalConstant:-10000,centralGravity:.2,
               springLength:140,springConstant:.03,damping:.12,avoidOverlap:.8},
    stabilization:{enabled:true,iterations:500,fit:true},
  },
  interaction:{hover:true,tooltipDelay:60,zoomView:true,hideEdgesOnDrag:false},
  edges:{smooth:{type:'continuous'}},
});

// ── stabilisation progress ────────────────────────────────────────────────────
const stabEl   = document.getElementById('stabilizing');
const stabFill = document.getElementById('stab-fill');
const stabPct  = document.getElementById('stab-pct');

net.on('stabilizationProgress', p => {
  const pct = Math.round((p.iterations / p.total) * 100);
  stabFill.style.width = pct + '%';
  stabPct.textContent  = p.iterations + ' / ' + p.total + ' iterations';
});

let stabDone = false;
function hideStab(){
  if(stabDone) return; stabDone = true;
  anime({targets: stabEl, opacity:[1,0], duration:500, easing:'easeOutQuad',
         complete:()=>{ stabEl.style.display='none'; }});
}
net.on('stabilized', ()=>{
  net.setOptions({physics:{enabled:false}});
  stabFill.style.width = '100%';
  stabPct.textContent  = 'Ready';
  setTimeout(hideStab, 500);
});
setTimeout(hideStab, 14000);  // fallback if event never fires

// ── keep canvas filling #gw after any layout change ───────────────────────────
new ResizeObserver(()=>{
  net.setSize(gc.offsetWidth+'px', gc.offsetHeight+'px');
  net.redraw();
}).observe(gw);

// re-draw after drag to prevent edge dropout
net.on('dragEnd', ()=> net.redraw());

// ── click ─────────────────────────────────────────────────────────────────────
net.on('click', p => { if(p.nodes.length) showDetail(p.nodes[0]); });

// ── navigation ────────────────────────────────────────────────────────────────
function goTo(id){
  net.selectNodes([id]);
  net.focus(id,{scale:Math.max(net.getScale(),.9),
                animation:{duration:420,easingFunction:'easeInOutQuad'}});
  showDetail(id);
}

// ── hamburger sidebar toggle ──────────────────────────────────────────────────
const sb = document.getElementById('sb');
let sbOpen = true;
document.getElementById('ham').onclick = ()=>{
  sbOpen = !sbOpen;
  anime({
    targets: sb,
    width: sbOpen ? 272 : 0,
    duration: 240,
    easing: 'easeInOutQuad',
    complete: ()=>{
      net.setSize(gc.offsetWidth+'px', gc.offsetHeight+'px');
      net.redraw();
    },
  });
};

// ── detail panel ─────────────────────────────────────────────────────────────
const dp = document.getElementById('dp');
let dpOpen = false;

document.getElementById('dp-close').onclick = closeDetail;

// delegated click for connection rows and wikilinks
document.getElementById('dp-body').addEventListener('click', e => {
  const t = e.target.closest('[data-go]');
  if(t) goTo(t.dataset.go);
});

function openPanel(){
  if(dpOpen) return;
  dpOpen = true;
  anime({
    targets: dp,
    translateX: ['100%','0%'],
    duration: 260,
    easing: 'easeOutQuad',
    complete: ()=>{
      net.setSize(gc.offsetWidth+'px', gc.offsetHeight+'px');
      net.redraw();
    },
  });
}

function closeDetail(){
  if(!dpOpen) return;
  dpOpen = false;
  anime({
    targets: dp,
    translateX: ['0%','100%'],
    duration: 220,
    easing: 'easeInQuad',
    complete: ()=>{
      net.setSize(gc.offsetWidth+'px', gc.offsetHeight+'px');
      net.redraw();
    },
  });
  net.unselectAll();
  setActive(null);
}

function showDetail(id){
  const n = NM[id]; if(!n) return;
  setActive(id);

  // header
  document.getElementById('dp-icon').innerHTML   = ICONS[n.ntype]||ICONS.document;
  document.getElementById('dp-title').textContent = n.label;
  const badge = document.getElementById('dp-badge');
  badge.textContent      = n.ntype;
  badge.style.background = (COLORS[n.ntype]||'#38bdf8')+'22';
  badge.style.color      = COLORS[n.ntype]||'#38bdf8';

  // metadata
  const rows = [['Type', n.ntype]];
  if(n.meta)  rows.push(['Info',  n.meta]);
  if(n.fpath) rows.push(['Path',  n.fpath]);
  rows.push(['Links', n.connections+(n.connections===1?' connection':' connections')]);
  if(n.depth) rows.push(['Level', 'H'+n.depth]);
  document.getElementById('dp-meta').innerHTML =
    rows.map(([k,v])=>
      `<div class="dmr"><span class="dmk">${esc(k)}</span>` +
      `<span class="dmv">${esc(String(v))}</span></div>`
    ).join('');

  // connections
  const seen = new Set(), conns = [];
  G.edges.forEach(e => {
    const other = e.from===id ? e.to : e.to===id ? e.from : null;
    if(other && !seen.has(other)){ seen.add(other); conns.push({id:other,etype:e.etype||'link'}); }
  });
  const connEl = document.getElementById('dp-conn');
  if(!conns.length){
    connEl.innerHTML = '<div style="font-size:12px;color:var(--muted)">No connections</div>';
  } else {
    connEl.innerHTML = conns.map(c=>{
      const cn = NM[c.id]; if(!cn) return '';
      return `<div class="cl" data-go="${esc(c.id)}">` +
             `<span class="cd" style="background:${COLORS[cn.ntype]||'#38bdf8'}"></span>` +
             `<span class="cn">${esc(cn.label)}</span>` +
             `<span class="ct">${esc(c.etype)}</span></div>`;
    }).join('');
  }

  // content with wikilinks rendered
  const sec = document.getElementById('s-cont');
  if(n.content){
    document.getElementById('dp-content').innerHTML = renderContent(n.content);
    sec.style.display = '';
  } else {
    sec.style.display = 'none';
  }

  openPanel();
}

// ── detail panel resize handle ────────────────────────────────────────────────
let resizing = false, rsX = 0, rsW = 0;
document.getElementById('dp-resize').addEventListener('mousedown', e=>{
  resizing=true; rsX=e.clientX; rsW=dp.offsetWidth;
  document.body.style.cursor='ew-resize';
  e.preventDefault();
});
document.addEventListener('mousemove', e=>{
  if(!resizing) return;
  const nw = Math.max(240, Math.min(720, rsW+(rsX-e.clientX)));
  dp.style.width = nw+'px';
  net.redraw();
});
document.addEventListener('mouseup', ()=>{
  if(resizing){ resizing=false; document.body.style.cursor=''; net.redraw(); }
});

// ── content + wikilinks ───────────────────────────────────────────────────────
const nameMap = {};
G.nodes.forEach(n => {
  const stem = n.label.replace(/\\.[^.]+$/, '').toLowerCase();
  nameMap[stem]                  = n.id;
  nameMap[n.label.toLowerCase()] = n.id;
});

function renderContent(txt){
  return esc(txt).replace(/\\[\\[([^\\]]+)\\]\\]/g, (_,inner)=>{
    const tid = nameMap[inner.toLowerCase()];
    if(tid) return `<span class="wl" data-go="${esc(tid)}">${esc(inner)}</span>`;
    return `<span class="wl-dead">[[${esc(inner)}]]</span>`;
  });
}

// ── sidebar ───────────────────────────────────────────────────────────────────
const ORDER  = ['folder','directory','document','code','section'];
const GLABEL = {folder:'Folders',directory:'Directories',document:'Documents',
                code:'Code Files',section:'Sections'};
let sortMode = 'az', query = '';

function buildSidebar(){
  const groups = {};
  G.nodes.forEach(n=>{ (groups[n.ntype]||(groups[n.ntype]=[])).push(n); });
  Object.values(groups).forEach(a=>sortArr(a));

  const list = document.getElementById('nlist');
  list.innerHTML = '';
  ORDER.forEach(type=>{
    const items = groups[type]; if(!items?.length) return;
    const grp = document.createElement('div');
    grp.className = 'grp';

    const hdr = document.createElement('div');
    hdr.className = 'grp-hdr';
    hdr.innerHTML =
      `<span class="ndot" style="background:${COLORS[type]};width:6px;height:6px"></span>` +
      `${GLABEL[type]||type}<span class="gcnt">${items.length}</span>` +
      `<span class="garrow">&#9660;</span>`;
    hdr.onclick = ()=> grp.classList.toggle('closed');

    const body = document.createElement('div');
    body.className = 'grp-items';
    items.forEach(n=>{
      const matches = !query ||
        n.label.toLowerCase().includes(query) ||
        (n.content||'').toLowerCase().includes(query);
      const el = document.createElement('div');
      el.className = 'ni'+(matches?'':' dim');
      el.dataset.id = n.id;
      el.innerHTML =
        `<span class="ndot" style="background:${COLORS[n.ntype]||'#38bdf8'}"></span>` +
        `<span class="nlbl">${esc(n.label)}</span>` +
        `<span class="nct">${n.connections}</span>`;
      el.onclick = ()=> goTo(n.id);
      body.appendChild(el);
    });

    grp.append(hdr, body);
    list.appendChild(grp);
  });
}

function sortArr(a){
  if(sortMode==='az')    a.sort((x,y)=>x.label.localeCompare(y.label));
  if(sortMode==='za')    a.sort((x,y)=>y.label.localeCompare(x.label));
  if(sortMode==='links') a.sort((x,y)=>y.connections-x.connections);
}

function setActive(id){
  document.querySelectorAll('.ni').forEach(el=>
    el.classList.toggle('on', el.dataset.id===id));
}

document.querySelectorAll('.sbtn').forEach(b=> b.onclick=()=>{
  document.querySelectorAll('.sbtn').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  sortMode = b.dataset.s;
  buildSidebar();
});

document.getElementById('search').oninput = e=>{
  query = e.target.value.toLowerCase().trim();
  buildSidebar();
  if(query){
    const hits = G.nodes.filter(n=>
      n.label.toLowerCase().includes(query)||
      (n.content||'').toLowerCase().includes(query)
    ).map(n=>n.id);
    net.selectNodes(hits);
  } else {
    net.unselectAll();
  }
};

// ── helpers ───────────────────────────────────────────────────────────────────
function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

buildSidebar();
</script>
</body>
</html>"""


# keep _render_pyvis stub so existing imports don't break
def _render_pyvis(G: nx.DiGraph, out_path: str, title: str):
    net = PyvisNetwork(
        height="100vh", width="100%", directed=True,
        bgcolor="#0f172a", font_color="#f1f5f9",
        notebook=False,
    )

    for nid, data in G.nodes(data=True):
        style = _NODE_STYLE.get(data.get("ntype", "document"), _NODE_STYLE["document"])
        depth = data.get("depth", 1)
        size  = style["size"] if data.get("ntype") != "section" else max(6, 15 - depth * 2)
        net.add_node(
            nid,
            label=data.get("label", nid),
            title=data.get("title", data.get("label", "")),
            color={"background": style["color"], "border": style["color"],
                   "highlight": {"background": "#ffffff", "border": style["color"]}},
            shape=style["shape"], size=size,
            font={"size": 11, "color": "#f1f5f9", "face": "Inter, Segoe UI, sans-serif"},
        )

    for src, dst, data in G.edges(data=True):
        related = data.get("etype") == "related"
        w = max(1, min(4, data.get("weight", 1) // 6)) if related else 1
        net.add_edge(
            src, dst, arrows="to",
            color={"color": "#7c3aed" if related else "#334155",
                   "highlight": "#a78bfa" if related else C_BLUE},
            dashes=related, width=w, title=data.get("title", ""),
        )

    net.set_options("""{
  "physics": {
    "solver": "barnesHut",
    "barnesHut": {
      "gravitationalConstant": -9000,
      "centralGravity": 0.25,
      "springLength": 130,
      "springConstant": 0.04,
      "damping": 0.09,
      "avoidOverlap": 0.6
    },
    "stabilization": { "iterations": 250 }
  },
  "interaction": { "hover": true, "tooltipDelay": 80 },
  "edges": { "smooth": { "type": "dynamic" } }
}""")

    try:
        net.save_graph(out_path)
    except AttributeError:
        net.write_html(out_path)

    _inject_header(out_path, title, G)


def _inject_header(filepath: str, title: str, G: nx.DiGraph):
    """Inject a Cohere-styled header bar into the pyvis HTML."""
    try:
        html    = Path(filepath).read_text(encoding="utf-8")
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()

        legend_items = [
            ("Folder",    "#f59e0b"),
            ("Document",  "#38bdf8"),
            ("Code",      "#34d399"),
            ("Section",   "#818cf8"),
            ("Related",   "#7c3aed"),
        ]
        legend_html = "".join(
            f'<span style="display:flex;align-items:center;gap:5px;'
            f'font-size:11px;color:#93939f">'
            f'<span style="width:9px;height:9px;border-radius:50%;'
            f'background:{c};flex-shrink:0"></span>{label}</span>'
            for label, c in legend_items
        )

        header = f"""
<style>
  body {{ margin:0; overflow:hidden; }}
  #gfy-bar {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
    background: {C_PURPLE_HDR};
    border-bottom: 1px solid rgba(255,255,255,0.08);
    padding: 0 24px;
    height: 54px;
    display: flex; align-items: center; gap: 16px;
    font-family: Inter, "Segoe UI", Arial, sans-serif;
  }}
  #gfy-bar .logo {{
    font-size: 20px; letter-spacing: -0.3px;
    color: #ffffff; font-weight: 600; white-space: nowrap;
  }}
  #gfy-bar .divider {{
    width: 1px; height: 22px; background: rgba(255,255,255,0.15);
    flex-shrink: 0;
  }}
  #gfy-bar .title-block {{ flex: 1; min-width: 0; }}
  #gfy-bar .doc-title {{
    color: #e2e8f0; font-size: 14px; font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  #gfy-bar .doc-meta {{
    color: #64748b; font-size: 11px; margin-top: 1px;
    font-family: "Courier New", monospace;
    letter-spacing: 0.2px; text-transform: uppercase;
  }}
  #gfy-legend {{
    display: flex; gap: 14px; align-items: center; flex-shrink: 0;
  }}
  #gfy-hint {{
    color: #475569; font-size: 11px; white-space: nowrap; flex-shrink: 0;
  }}
</style>
<div id="gfy-bar">
  <span class="logo">&#9735; Graphify</span>
  <div class="divider"></div>
  <div class="title-block">
    <div class="doc-title">{title}</div>
    <div class="doc-meta">{n_nodes} nodes &middot; {n_edges} edges</div>
  </div>
  <div id="gfy-legend">{legend_html}</div>
  <div class="divider"></div>
  <div id="gfy-hint">Scroll to zoom &middot; Drag to pan</div>
</div>
<div style="height:54px"></div>
"""
        html = html.replace("<body>", f"<body>\n{header}", 1)
        html = html.replace("<title>network</title>",
                            f"<title>{title} — Knowledge Graph</title>", 1)
        Path(filepath).write_text(html, encoding="utf-8")
    except Exception:
        pass


def _render_fallback_html(G: nx.DiGraph, out_path: str, title: str):
    """Minimal vis.js fallback when pyvis is not installed."""
    nodes_json = json.dumps(
        [{"id": n, **{k: str(v) for k, v in d.items()}} for n, d in G.nodes(data=True)],
        indent=2,
    )
    edges_json = json.dumps([{"from": u, "to": v} for u, v in G.edges()], indent=2)
    cmap = json.dumps({k: v["color"] for k, v in _NODE_STYLE.items()})

    Path(out_path).write_text(f"""<!DOCTYPE html>
<html><head>
  <meta charset="utf-8">
  <title>{title} — Knowledge Graph</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{ background:#0f172a; margin:0; font-family:sans-serif; }}
    #network {{ width:100vw; height:100vh; }}
  </style>
</head>
<body>
<div id="network"></div>
<script>
  const cmap = {cmap};
  const nodes = new vis.DataSet({nodes_json}.map(n => ({{
    id: n.id, label: n.label || n.id,
    color: cmap[n.ntype] || "#38bdf8",
    title: n.title || n.label || "",
  }})));
  const edges = new vis.DataSet({edges_json}.map((e,i) => ({{
    id:i, from:e.from, to:e.to, arrows:"to"
  }})));
  new vis.Network(document.getElementById("network"), {{nodes, edges}}, {{
    physics:{{barnesHut:{{gravitationalConstant:-8000}}}},
    edges:{{color:"#334155"}},
    nodes:{{font:{{color:"#f1f5f9"}}}},
  }});
</script>
</body></html>""", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# UI — Cohere-inspired design (light canvas, 22px cards, purple header band)
# ─────────────────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def _section_label(parent, text: str):
    """Uppercase muted section label — CohereMono style."""
    ctk.CTkLabel(
        parent, text=text.upper(),
        font=ctk.CTkFont("Courier New", 10),
        text_color=C_MUTED,
    ).pack(anchor="w", pady=(0, 5))


def _divider(parent):
    ctk.CTkFrame(parent, height=1, fg_color=C_BORDER_LT).pack(fill="x", pady=10)


class App:
    def __init__(self):
        if _DND:
            self.root = TkinterDnD.Tk()
            self.root.configure(bg=C_WHITE)
        else:
            self.root = ctk.CTk()
            self.root.configure(fg_color=C_WHITE)

        self.root.title("Graphify")
        self.root.geometry("840x680")
        self.root.minsize(720, 580)

        # State
        self._input_folder  = tk.StringVar()
        self._output_folder = tk.StringVar(value=str(Path.home() / "graphify_output"))
        self._running       = False

        # Options
        self._opt_parse   = tk.BooleanVar(value=True)
        self._opt_text    = tk.BooleanVar(value=True)
        self._opt_recurse = tk.BooleanVar(value=True)
        self._opt_code    = tk.BooleanVar(value=False)
        self._opt_related = tk.BooleanVar(value=True)
        self._opt_json    = tk.BooleanVar(value=False)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_body()

    def _build_header(self):
        """Deep purple hero band — the Cohere signature contrast section."""
        hdr = ctk.CTkFrame(self.root, fg_color=C_PURPLE_HDR, corner_radius=0, height=72)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            inner,
            text="\u2735  Graphify",   # ✵
            font=ctk.CTkFont("Segoe UI", 22, "bold"),
            text_color="#ffffff",
        ).pack(side="left", padx=(0, 16))

        sep = ctk.CTkFrame(inner, width=1, height=28, fg_color="#3d2060")
        sep.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(
            inner,
            text="Knowledge Graph Builder",
            font=ctk.CTkFont("Segoe UI", 13),
            text_color="#8b72b8",
        ).pack(side="left")


    def _build_body(self):
        """White canvas body with left main column and right options card."""
        body = ctk.CTkFrame(self.root, fg_color=C_SNOW, corner_radius=0)
        body.pack(fill="both", expand=True)

        wrap = ctk.CTkFrame(body, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=20)

        left = ctk.CTkFrame(wrap, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, 16))

        right = ctk.CTkFrame(
            wrap,
            fg_color=C_WHITE,
            border_width=1, border_color=C_BORDER_LT,
            corner_radius=22,
            width=242,
        )
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        self._build_drop_zone(left)
        self._build_output_row(left)
        _divider(left)
        self._build_progress_run(left)
        self._build_options(right)

    def _build_drop_zone(self, parent):
        """22px-radius drop card — the Cohere card signature."""
        self._dz = ctk.CTkFrame(
            parent,
            fg_color=C_WHITE,
            border_width=1, border_color=C_BORDER_MID,
            corner_radius=22,
            height=178,
        )
        self._dz.pack(fill="x", pady=(0, 12))
        self._dz.pack_propagate(False)

        self._dz_icon = ctk.CTkLabel(
            self._dz, text="📂",
            font=ctk.CTkFont(size=38),
        )
        self._dz_icon.pack(pady=(22, 4))

        dnd_hint = "Drop a folder here  —  or click to browse" if _DND \
                   else "Click to browse for a folder"
        self._dz_hint = ctk.CTkLabel(
            self._dz, text=dnd_hint,
            font=ctk.CTkFont("Segoe UI", 13),
            text_color=C_MUTED,
        )
        self._dz_hint.pack()

        self._dz_path = ctk.CTkLabel(
            self._dz, text="",
            font=ctk.CTkFont("Courier New", 11),
            text_color=C_BLUE,
        )
        self._dz_path.pack(pady=(4, 0))

        for w in (self._dz, self._dz_icon, self._dz_hint, self._dz_path):
            w.bind("<Button-1>", lambda _e: self._browse_input())
            w.bind("<Enter>",    self._dz_enter)
            w.bind("<Leave>",    self._dz_leave)

        if _DND:
            self._dz.drop_target_register(TkinterDnD.DND_FILES)
            self._dz.dnd_bind("<<Drop>>", self._on_drop)

    def _dz_enter(self, _e=None):
        self._dz.configure(border_color=C_BLUE, fg_color="#f0f6ff")

    def _dz_leave(self, _e=None):
        self._dz.configure(border_color=C_BORDER_MID, fg_color=C_WHITE)

    def _build_output_row(self, parent):
        """Output folder card."""
        card = ctk.CTkFrame(
            parent,
            fg_color=C_WHITE,
            border_width=1, border_color=C_BORDER_LT,
            corner_radius=22,
        )
        card.pack(fill="x")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)

        _section_label(inner, "Output folder")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkEntry(
            row,
            textvariable=self._output_folder,
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=C_SNOW,
            border_color=C_BORDER_MID,
            border_width=1,
            text_color=C_NEAR_BLK,
            corner_radius=8,
        ).pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            row, text="Browse", width=82,
            fg_color="transparent",
            border_width=1, border_color=C_BORDER_MID,
            text_color=C_NEAR_BLK,
            hover_color=C_BORDER_LT,
            font=ctk.CTkFont("Segoe UI", 12),
            corner_radius=8,
            command=self._browse_output,
        ).pack(side="right")

    def _build_progress_run(self, parent):
        """Status label, thin progress bar, and dark pill CTA button."""
        self._status_lbl = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=C_MUTED,
        )
        self._status_lbl.pack(anchor="w", pady=(0, 6))

        self._pbar = ctk.CTkProgressBar(
            parent,
            height=4,
            corner_radius=2,
            progress_color=C_BLUE,
            fg_color=C_BORDER_LT,
        )
        self._pbar.pack(fill="x", pady=(0, 16))
        self._pbar.set(0)

        # Dark solid pill button — the primary CTA per DESIGN.md
        self._run_btn = ctk.CTkButton(
            parent,
            text="Build Graph",
            font=ctk.CTkFont("Segoe UI", 14, "bold"),
            height=46,
            corner_radius=23,       # pill shape
            fg_color=C_BLACK,
            hover_color=C_BLUE,     # Interaction Blue on hover
            text_color=C_WHITE,
            command=self._run,
        )
        self._run_btn.pack(fill="x")

    def _build_options(self, parent):
        """Right-hand options card with checkboxes."""
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=20)

        _section_label(inner, "Graph Options")

        opts = [
            ("Parse document structure",  self._opt_parse,
             "Detect headings & sections within files"),
            ("Show text previews",         self._opt_text,
             "Include snippets in hover tooltips"),
            ("Recurse subdirectories",     self._opt_recurse,
             "Process all nested folders"),
            ("Include code files",         self._opt_code,
             "Process .py, .js, .ts and similar"),
            ("Link related content",       self._opt_related,
             "Draw edges between files sharing keywords"),
            ("Export JSON",                self._opt_json,
             "Also save a machine-readable .json file"),
        ]

        for label, var, tip in opts:
            grp = ctk.CTkFrame(inner, fg_color="transparent")
            grp.pack(fill="x", pady=(0, 10))

            ctk.CTkCheckBox(
                grp,
                text=label,
                variable=var,
                font=ctk.CTkFont("Segoe UI", 12),
                text_color=C_NEAR_BLK,
                fg_color=C_BLUE,
                hover_color=C_BLUE,
                checkmark_color=C_WHITE,
                border_color=C_BORDER_MID,
                checkbox_width=16,
                checkbox_height=16,
                corner_radius=4,
            ).pack(anchor="w")

            ctk.CTkLabel(
                grp,
                text=tip,
                font=ctk.CTkFont("Segoe UI", 10),
                text_color=C_MUTED,
                wraplength=190,
                justify="left",
            ).pack(anchor="w", padx=(22, 0))

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_drop(self, event):
        path = event.data.strip().strip("{}").strip('"')
        if os.path.isdir(path):
            self._set_input(path)
        else:
            messagebox.showwarning("Not a folder",
                                   f"Please drop a folder, not a file:\n{path}",
                                   parent=self.root)

    def _browse_input(self):
        folder = filedialog.askdirectory(title="Select input folder", parent=self.root)
        if folder:
            self._set_input(folder)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Select output folder", parent=self.root)
        if folder:
            self._output_folder.set(folder)

    def _set_input(self, path: str):
        self._input_folder.set(path)
        self._dz_icon.configure(text="✅")
        self._dz_hint.configure(text=Path(path).name, text_color=C_NEAR_BLK)
        self._dz_path.configure(text=path)
        self._dz_leave()

    # Thread-safe helpers

    def _set_status(self, text: str):
        self.root.after(0, lambda t=text: self._status_lbl.configure(text=t))

    def _set_progress(self, value: float):
        self.root.after(0, lambda v=value: self._pbar.set(v))

    # ── Run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if self._running:
            return
        folder = self._input_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("No folder selected",
                                 "Please select or drop a folder first.",
                                 parent=self.root)
            return
        output = self._output_folder.get().strip()
        if not output:
            messagebox.showerror("No output folder",
                                 "Please specify an output folder.",
                                 parent=self.root)
            return

        options = {
            "parse_structure": self._opt_parse.get(),
            "include_text":    self._opt_text.get(),
            "recurse":         self._opt_recurse.get(),
            "include_code":    self._opt_code.get(),
            "link_related":    self._opt_related.get(),
            "export_json":     self._opt_json.get(),
        }

        self._running = True
        self._run_btn.configure(text="Processing…", state="disabled",
                                fg_color=C_MUTED)
        self._pbar.set(0)

        def worker():
            try:
                out = build_graph(
                    folder, output, options,
                    progress_cb=self._set_progress,
                    status_cb=self._set_status,
                )
                self.root.after(0, lambda f=out: self._on_done(f))
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, out_file: str):
        self._running = False
        self._run_btn.configure(text="Build Graph", state="normal",
                                fg_color=C_BLACK)
        self._set_status(f"Saved \u2192 {out_file}")

        if messagebox.askyesno(
            "Graph ready",
            f"Knowledge graph saved to:\n\n{out_file}\n\nOpen in browser?",
            parent=self.root,
        ):
            webbrowser.open(Path(out_file).as_uri())

    def _on_error(self, msg: str):
        self._running = False
        self._run_btn.configure(text="Build Graph", state="normal",
                                fg_color=C_BLACK)
        self._set_status(f"Error: {msg}")
        messagebox.showerror("Processing error", msg, parent=self.root)

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().run()
