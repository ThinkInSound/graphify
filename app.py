"""
Graphify Desktop App
====================
Drop a folder onto the window to generate an interactive knowledge graph.

Requirements:
    pip install customtkinter tkinterdnd2
Optional (richer media metadata):
    pip install Pillow mutagen pypdf
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

# ── Optional media-metadata libraries ────────────────────────────────────────
try:
    from PIL import Image as PILImage          # type: ignore
    _PIL = True
except ImportError:
    _PIL = False

try:
    import mutagen                              # type: ignore
    _MUTAGEN = True
except ImportError:
    _MUTAGEN = False

try:
    import pypdf                               # type: ignore
    _PYPDF = True
except ImportError:
    try:
        import PyPDF2 as pypdf                 # type: ignore
        _PYPDF = True
    except ImportError:
        _PYPDF = False

import networkx as nx

# ─────────────────────────────────────────────────────────────────────────────
# Palette — Cohere DESIGN.md structure + neon node accent colours
# ─────────────────────────────────────────────────────────────────────────────

# Node accent palette (colorhunt.co/palette/ff1700ff8e00ffe40006ff00)
N_RED    = "#FF1700"
N_ORANGE = "#FF8E00"
N_YELLOW = "#FFE400"
N_GREEN  = "#06FF00"

# GUI palette — two modes
# Light: colorhunt.co/palette/f9f8f6efe9e3d9cfc7c9b59c
_LIGHT = dict(
    bg="#F9F8F6", bg_alt="#EFE9E3", surface="#F9F8F6",
    border_lt="#D9CFC7", border_md="#C9B59C",
    text="#2a1f16", muted="#8a7a6a",
    accent=N_ORANGE,
    hdr="#EFE9E3", hdr_sep="#D9CFC7", hdr_sub="#8a7a6a",
    btn="#2a1f16", btn_hover=N_ORANGE, btn_text="#F9F8F6",
    dz_hover="#EFE9E3",
)
# Dark: colorhunt.co/palette/838383ad9d9dd9adadfccbcb (accents over dark base)
_DARK = dict(
    bg="#161212", bg_alt="#1e1a1a", surface="#231e1e",
    border_lt="#2e2828", border_md="#383030",
    text="#FCCBCB", muted="#AD9D9D",
    accent=N_ORANGE,
    hdr="#161212", hdr_sep="#2e2828", hdr_sub="#D9ADAD",
    btn=N_ORANGE, btn_hover=N_RED, btn_text="#161212",
    dz_hover="#231e1e",
)

_MODE  = "light"   # module-level; App mutates this

def _c(key: str) -> str:
    """Return current-mode colour by key."""
    return (_LIGHT if _MODE == "light" else _DARK)[key]

# ─────────────────────────────────────────────────────────────────────────────
# File-type sets
# ─────────────────────────────────────────────────────────────────────────────

TEXT_EXT  = {".txt", ".md", ".rst", ".markdown", ".text"}
CODE_EXT  = {".py", ".js", ".ts", ".jsx", ".tsx", ".java",
             ".cpp", ".c", ".h", ".go", ".rs", ".rb", ".cs", ".swift"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp",
             ".bmp", ".tiff", ".tif", ".ico", ".svg"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff", ".aif"}
PDF_EXT   = {".pdf"}
ALL_MEDIA = IMAGE_EXT | AUDIO_EXT | PDF_EXT

CONTENT_LIMIT = 6_000   # chars stored per node for the detail panel

# ─────────────────────────────────────────────────────────────────────────────
# Metadata extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _image_meta(path: Path) -> dict:
    info: dict = {}
    kb = path.stat().st_size / 1024
    info["size"] = f"{kb:.1f} KB"
    if _PIL:
        try:
            with PILImage.open(path) as im:
                info["dimensions"] = f"{im.width} × {im.height} px"
                info["mode"]       = im.mode
                info["format"]     = im.format or path.suffix[1:].upper()
                exif = getattr(im, "_getexif", None)
                if exif:
                    raw = exif()
                    if raw:
                        from PIL.ExifTags import TAGS
                        for tag_id, val in raw.items():
                            tag = TAGS.get(tag_id, tag_id)
                            if tag in ("Make","Model","DateTime","GPSInfo"):
                                info[str(tag)] = str(val)[:80]
        except Exception:
            pass
    return info


def _audio_meta(path: Path) -> dict:
    info: dict = {}
    kb = path.stat().st_size / 1024
    info["size"] = f"{kb:.1f} KB"
    if _MUTAGEN:
        try:
            # easy=True normalises tag keys to lowercase across MP3 (ID3),
            # FLAC/OGG (Vorbis), MP4/AAC, AIFF — so af.get("artist") works
            # for all formats instead of needing format-specific frame names.
            af = mutagen.File(path, easy=True)
            if af is None:
                af = mutagen.File(path)   # fallback for formats without easy support
            if af is not None:
                if hasattr(af, "info"):
                    ai = af.info
                    if hasattr(ai, "length"):
                        s = int(ai.length)
                        info["duration"] = f"{s//60}:{s%60:02d}"
                    if hasattr(ai, "bitrate") and ai.bitrate:
                        info["bitrate"] = f"{ai.bitrate//1000} kbps"
                    if hasattr(ai, "channels"):
                        info["channels"] = str(ai.channels)
                    if hasattr(ai, "sample_rate"):
                        info["sample_rate"] = f"{ai.sample_rate} Hz"
                for tag in ("title", "artist", "album", "tracknumber", "date", "genre", "bpm"):
                    v = af.get(tag) or af.get(tag.upper())
                    if v:
                        val = str(v[0] if isinstance(v, list) else v)[:80]
                        if tag == "bpm":
                            try:
                                val = str(int(float(val)))
                            except ValueError:
                                pass
                        info[tag] = val
        except Exception:
            pass
    return info


def _pdf_meta(path: Path) -> dict:
    info: dict = {}
    kb = path.stat().st_size / 1024
    info["size"] = f"{kb:.1f} KB"
    if _PYPDF:
        try:
            with open(path, "rb") as fh:
                reader = pypdf.PdfReader(fh)
                info["pages"] = str(len(reader.pages))
                meta = reader.metadata
                if meta:
                    for k in ("/Title","/Author","/Subject","/Creator","/CreationDate"):
                        v = getattr(meta, k.strip("/").lower(), None) or meta.get(k)
                        if v:
                            info[k.strip("/")] = str(v)[:80]
                # extract first-page text snippet
                try:
                    txt = reader.pages[0].extract_text() or ""
                    if txt.strip():
                        info["_preview"] = txt[:CONTENT_LIMIT]
                except Exception:
                    pass
        except Exception:
            pass
    return info

# ─────────────────────────────────────────────────────────────────────────────
# Processing logic
# ─────────────────────────────────────────────────────────────────────────────

def _collect_files(folder: str, recurse: bool, include_code: bool, include_media: bool):
    exts = TEXT_EXT | (CODE_EXT if include_code else set()) | (ALL_MEDIA if include_media else set())
    root = Path(folder)
    if recurse:
        return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts)
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _parse_markdown_sections(filepath: Path, include_text: bool):
    try:
        lines = filepath.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    sections, cur_heading, cur_text = [], None, []
    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if cur_heading:
                sections.append({"label": cur_heading["label"],
                                  "level": cur_heading["level"],
                                  "text":  "\n".join(cur_text[:6]) if include_text else ""})
            cur_heading = {"label": m.group(2).strip(), "level": len(m.group(1))}
            cur_text = []
        elif line.strip() and cur_heading:
            cur_text.append(line.strip())
    if cur_heading:
        sections.append({"label": cur_heading["label"],
                          "level": cur_heading["level"],
                          "text":  "\n".join(cur_text[:6]) if include_text else ""})
    return sections


def _find_keyword_links(files, threshold: int = 4):
    STOP = {"the","a","an","is","in","it","of","to","and","or","for","on","at","by",
            "be","as","are","was","were","with","that","this","from","they","have",
            "had","not","but","you","your","our","their","its","will","can","may",
            "has","we","he","she","if","do","so","more","use","used","also"}
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


def build_graph(folder: str, output: str, options: dict, progress_cb, status_cb) -> str:
    folder_path = Path(folder)
    output_dir  = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    status_cb("Scanning folder…")
    progress_cb(0.04)

    files = _collect_files(folder, options["recurse"],
                           options["include_code"], options.get("include_media", True))
    if not files:
        raise ValueError(f"No supported files found in:\n{folder}")

    status_cb(f"Found {len(files)} file(s) — building graph…")
    progress_cb(0.10)

    G = nx.DiGraph()
    root_id = "__ROOT__"
    G.add_node(root_id, label=folder_path.name, ntype="folder",
               content="", fpath=str(folder_path), meta="", props={})

    file_node_map: dict = {}

    for idx, filepath in enumerate(files):
        progress_cb(0.10 + 0.55 * (idx / len(files)))
        status_cb(f"Processing  {filepath.name}")

        rel   = filepath.relative_to(folder_path)
        fid   = f"FILE::{rel}"
        ext   = filepath.suffix.lower()
        kb    = filepath.stat().st_size / 1024

        # Determine node type + metadata
        if ext in CODE_EXT:
            ntype = "code"
            props = {"size": f"{kb:.1f} KB", "ext": ext[1:].upper()}
        elif ext in IMAGE_EXT:
            ntype = "image"
            props = _image_meta(filepath)
        elif ext in AUDIO_EXT:
            ntype = "audio"
            props = _audio_meta(filepath)
        elif ext in PDF_EXT:
            ntype = "pdf"
            props = _pdf_meta(filepath)
        else:
            ntype = "document"
            props = {"size": f"{kb:.1f} KB", "ext": ext[1:].upper()}

        # Build meta summary string
        meta_parts = []
        if ext:
            meta_parts.append(ext[1:].upper())
        meta_parts.append(f"{kb:.1f} KB")
        if "dimensions" in props:
            meta_parts.append(props["dimensions"])
        if "duration" in props:
            meta_parts.append(props["duration"])
        if "artist" in props:
            meta_parts.append(props["artist"])
        if "bpm" in props:
            meta_parts.append(f"{props['bpm']} BPM")
        if "pages" in props:
            meta_parts.append(f"{props['pages']} pages")
        meta = "  ·  ".join(meta_parts)

        # Read text content for detail panel
        content = props.pop("_preview", "")
        if not content and ext in (TEXT_EXT | CODE_EXT):
            try:
                raw = filepath.read_text(encoding="utf-8", errors="ignore")
                content = raw[:CONTENT_LIMIT]
                if len(raw) > CONTENT_LIMIT:
                    content += f"\n\n… ({len(raw) - CONTENT_LIMIT:,} more chars)"
            except Exception:
                pass

        # Build directory chain
        parent_id = root_id
        parts = rel.parts
        for depth in range(len(parts) - 1):
            dir_id = "DIR::" + "/".join(parts[: depth + 1])
            if dir_id not in G:
                G.add_node(dir_id, label=parts[depth], ntype="directory",
                           content="", fpath="", meta="", props={})
                G.add_edge(parent_id, dir_id)
            parent_id = dir_id

        G.add_node(fid, label=filepath.name, ntype=ntype,
                   fpath=str(filepath), content=content,
                   meta=meta, props=props)
        G.add_edge(parent_id, fid)
        file_node_map[str(filepath)] = fid

        # Parse markdown structure
        if options["parse_structure"] and ext in TEXT_EXT:
            sections    = _parse_markdown_sections(filepath, options["include_text"])
            level_stack = {0: fid}
            for s_idx, sec in enumerate(sections):
                sid = f"{fid}::S{s_idx}"
                G.add_node(sid, label=sec["label"], ntype="section",
                           depth=sec["level"], content=sec["text"],
                           fpath="", meta=f"H{sec['level']} section", props={})
                pl = sec["level"] - 1
                while pl > 0 and pl not in level_stack:
                    pl -= 1
                G.add_edge(level_stack.get(pl, fid), sid)
                level_stack[sec["level"]] = sid

    # Wikilinks
    status_cb("Detecting wikilinks…")
    progress_cb(0.68)
    name_map: dict = {}
    for nid, data in G.nodes(data=True):
        fp = data.get("fpath", "")
        if fp and data.get("ntype") in ("document", "code", "pdf"):
            p = Path(fp)
            name_map[p.stem.lower()] = nid
            name_map[p.name.lower()] = nid
    for nid, data in G.nodes(data=True):
        for m in re.finditer(r"\[\[([^\]|#\n]+)\]\]", data.get("content", "")):
            target = name_map.get(m.group(1).strip().lower())
            if target and target != nid and not G.has_edge(nid, target):
                G.add_edge(nid, target, etype="wikilink", title=f"[[{m.group(1)}]]")

    # Keyword similarity links
    if options["link_related"]:
        status_cb("Finding related content…")
        progress_cb(0.76)
        text_files = [f for f in files if f.suffix.lower() in TEXT_EXT]
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


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

_NODE_STYLE = {
    "folder":    {"color": N_RED,    "size": 28},
    "directory": {"color": N_ORANGE, "size": 20},
    "document":  {"color": N_YELLOW, "size": 16},
    "code":      {"color": N_GREEN,  "size": 16},
    "image":     {"color": N_ORANGE, "size": 14},
    "audio":     {"color": N_RED,    "size": 14},
    "pdf":       {"color": N_YELLOW, "size": 14},
    "section":   {"color": "#555555","size": 9},
}


def _render_html(G: nx.DiGraph, out_path: str, title: str):
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
            "props":       data.get("props", {}),
            "depth":       depth,
            "connections": G.degree(nid),
            "color":       style["color"],
            "size":        size,
        })

    edges_data = []
    for src, dst, data in G.edges(data=True):
        edges_data.append({
            "from":   src,  "to":    dst,
            "etype":  data.get("etype", "default"),
            "weight": data.get("weight", 1),
            "title":  data.get("title", ""),
        })

    graph_json = json.dumps({"nodes": nodes_data, "edges": edges_data},
                            ensure_ascii=False)
    graph_json = graph_json.replace("</", "<\\/")

    html = _HTML_TEMPLATE
    html = html.replace("__TITLE__",    title)
    html = html.replace("__N_NODES__",  str(G.number_of_nodes()))
    html = html.replace("__N_EDGES__",  str(G.number_of_edges()))
    html = html.replace("__GRAPH_JSON__", graph_json)
    Path(out_path).write_text(html, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# HTML template — three-panel SPA with dark/light toggle
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<title>__TITLE__ — Graphify</title>
<style>
/* ── theme tokens ──────────────────────────────────────────────────────────── */
:root {
  --r: #FF1700; --o: #FF8E00; --y: #FFE400; --g: #06FF00;
}
[data-theme="dark"] {
  --gbg: #161212; --ui: #1e1a1a; --ui2: #231e1e;
  --bdr: #2e2828; --bdr2: #383030;
  --txt: #FCCBCB; --txt2: #AD9D9D;
  --hdr: #161212;
}
[data-theme="light"] {
  --gbg: #F9F8F6; --ui: #EFE9E3; --ui2: #F9F8F6;
  --bdr: #D9CFC7; --bdr2: #C9B59C;
  --txt: #2a1f16; --txt2: #8a7a6a;
  --hdr: #EFE9E3;
}

/* ── reset ─────────────────────────────────────────────────────────────────── */
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;
     background:var(--gbg);height:100vh;display:flex;
     flex-direction:column;overflow:hidden;color:var(--txt)}

/* ── header ────────────────────────────────────────────────────────────────── */
#hdr{height:44px;background:var(--hdr);display:flex;align-items:center;
     padding:0 14px;gap:10px;flex-shrink:0}
.hbtn{background:none;border:none;color:rgba(255,255,255,.4);cursor:pointer;
      font-size:15px;padding:3px 5px;line-height:1;border-radius:4px;flex-shrink:0}
.hbtn:hover{color:rgba(255,255,255,.85);background:rgba(255,255,255,.06)}
.hlogo{font-size:13px;font-weight:700;color:var(--o);letter-spacing:.1px;white-space:nowrap}
.hsep{width:1px;height:14px;background:rgba(255,255,255,.1);flex-shrink:0}
.htitle{color:rgba(255,255,255,.35);font-size:12px;flex:1;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
.hstats{font-size:10px;color:rgba(255,255,255,.18);white-space:nowrap}
#theme-btn{margin-left:auto;font-size:14px}

/* ── layout ────────────────────────────────────────────────────────────────── */
#layout{display:flex;flex:1;overflow:hidden;position:relative}

/* ── sidebar ───────────────────────────────────────────────────────────────── */
#sb{width:248px;background:var(--ui);border-right:1px solid var(--bdr);
    display:flex;flex-direction:column;flex-shrink:0;overflow:hidden}
#sb-top{padding:9px 11px 7px;border-bottom:1px solid var(--bdr);flex-shrink:0}
#search{width:100%;padding:5px 9px;border:1px solid var(--bdr2);border-radius:5px;
        font-size:12px;font-family:inherit;color:var(--txt);background:var(--ui2);
        outline:none;transition:border-color .15s}
#search:focus{border-color:var(--o)}
#search::placeholder{color:var(--txt2)}
#sortbar,#sortbar2{display:flex;gap:3px;margin-top:4px}
.sbtn{flex:1;padding:3px 0;border:1px solid var(--bdr2);border-radius:4px;
      font-size:9px;letter-spacing:.2px;text-transform:uppercase;
      color:var(--txt2);background:transparent;cursor:pointer;transition:all .14s}
.sbtn:hover,.sbtn.on{border-color:var(--o);color:var(--o)}
/* audio player */
#dp-audio{width:100%;border-radius:6px;margin-top:6px;accent-color:var(--o);
           display:block;background:var(--ui2);outline:none}
#nlist{flex:1;overflow-y:auto;padding:3px 0}
/* groups */
.grp-hdr{display:flex;align-items:center;gap:6px;padding:6px 11px 4px;
          cursor:pointer;user-select:none;font-size:9px;letter-spacing:.25px;
          text-transform:uppercase;color:var(--txt2)}
.grp-hdr:hover{color:var(--txt)}
.garrow{font-size:7px;transition:transform .18s;margin-left:auto;opacity:.4}
.grp.closed .garrow{transform:rotate(-90deg)}
.grp.closed .grp-items{display:none}
.gcnt{background:var(--bdr2);color:var(--txt2);border-radius:99px;
      padding:1px 6px;font-size:9px}
.ni{display:flex;align-items:center;gap:7px;padding:4px 11px;cursor:pointer;
    transition:background .1s;border-left:2px solid transparent}
.ni:hover{background:var(--ui2)}
.ni.on{background:var(--ui2);border-left-color:var(--o)}
.ni.dim{opacity:.18}
.ndot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.nlbl{font-size:11px;color:var(--txt);overflow:hidden;text-overflow:ellipsis;
      white-space:nowrap;flex:1}
.nct{font-size:9px;color:var(--txt2);white-space:nowrap}

/* ── graph canvas ──────────────────────────────────────────────────────────── */
#gw{flex:1;position:relative;overflow:hidden;min-width:0}
#gc{width:100%;height:100%}
/* force canvas bg to match theme */
[data-theme="dark"]  #gc canvas { background:#161212 !important }
[data-theme="light"] #gc canvas { background:#F9F8F6 !important }
#ghost{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
       color:rgba(252,203,203,.12);font-size:10px;pointer-events:none;white-space:nowrap}
[data-theme="light"] #ghost{color:rgba(42,31,22,.15)}

/* stabilising overlay */
#stabilizing{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
              background:rgba(22,18,18,.9);color:var(--o);
              padding:18px 24px;border-radius:10px;text-align:center;
              font-size:11px;letter-spacing:.1px;pointer-events:none;
              min-width:210px;border:1px solid rgba(255,142,0,.15)}
[data-theme="light"] #stabilizing{background:rgba(249,248,246,.94);
  color:#8a4a00;border-color:rgba(255,142,0,.3)}
#stab-txt{display:block;margin-bottom:10px}
#stab-track{background:rgba(255,255,255,.08);border-radius:99px;height:3px;margin-bottom:6px}
[data-theme="light"] #stab-track{background:rgba(0,0,0,.08)}
#stab-fill{background:var(--o);border-radius:99px;height:3px;width:0%;
           transition:width .1s linear}
#stab-pct{font-size:9px;color:rgba(255,142,0,.5)}

/* ── detail panel (fixed overlay) ─────────────────────────────────────────── */
#dp{position:fixed;right:0;top:44px;bottom:0;width:330px;
    background:var(--ui);border-left:1px solid var(--bdr);
    display:flex;flex-direction:column;overflow:hidden;
    transform:translateX(100%);z-index:100}
#dp-resize{position:absolute;left:0;top:0;bottom:0;width:5px;
           cursor:ew-resize;z-index:10}
#dp-resize:hover{background:rgba(255,142,0,.2)}
#dp-hdr{padding:12px 14px 9px;border-bottom:1px solid var(--bdr);flex-shrink:0}
#dp-close{float:right;background:none;border:none;font-size:16px;cursor:pointer;
           color:var(--txt2);line-height:1;padding:0}
#dp-close:hover{color:var(--txt)}
#dp-icon{font-size:18px;display:block;margin-bottom:3px}
#dp-title{font-size:13px;font-weight:600;color:var(--txt);
           word-break:break-word;margin-right:18px;line-height:1.3}
#dp-badge{display:inline-block;margin-top:4px;padding:2px 7px;border-radius:3px;
           font-size:9px;letter-spacing:.3px;text-transform:uppercase;
           background:var(--bdr2);color:var(--txt2)}
#dp-body{flex:1;overflow-y:auto;padding:11px 14px}
.ds{margin-bottom:14px}
.ds-lbl{font-size:9px;letter-spacing:.3px;text-transform:uppercase;
         color:var(--txt2);margin-bottom:5px;opacity:.7}
.dmr{display:flex;gap:5px;align-items:baseline;margin-bottom:3px;font-size:11px}
.dmk{color:var(--txt2);min-width:60px;flex-shrink:0;font-size:10px}
.dmv{color:var(--txt);word-break:break-all;font-family:"Courier New",monospace;font-size:10px}
/* image preview in detail */
#dp-img{max-width:100%;border-radius:6px;margin-bottom:10px;border:1px solid var(--bdr)}
.cl{display:flex;align-items:center;gap:6px;padding:4px 7px;border-radius:5px;
    cursor:pointer;margin-bottom:2px;transition:background .1s}
.cl:hover{background:var(--ui2)}
.cl .cd{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.cl .cn{font-size:11px;color:var(--txt);flex:1;overflow:hidden;
         text-overflow:ellipsis;white-space:nowrap}
.cl .ct{font-size:9px;color:var(--txt2)}
.cl:hover .cn{color:var(--o)}
#dp-content{font-size:11px;line-height:1.65;color:var(--txt);
             white-space:pre-wrap;word-break:break-word;background:var(--ui2);
             border-radius:5px;padding:10px;max-height:280px;overflow-y:auto;tab-size:2}
.wl{color:var(--o);cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.wl:hover{opacity:.7}
.wl-dead{color:var(--txt2);text-decoration:underline dashed;
          text-underline-offset:2px;cursor:default}

/* scrollbars */
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--bdr2);border-radius:99px}
</style>
</head>
<body>

<div id="hdr">
  <button class="hbtn" id="ham" title="Toggle sidebar">&#9776;</button>
  <span class="hlogo">&#10021; Graphify</span>
  <div class="hsep"></div>
  <span class="htitle">__TITLE__</span>
  <span class="hstats">__N_NODES__ &middot; __N_EDGES__</span>
  <button class="hbtn" id="theme-btn" title="Toggle light / dark">&#9680;</button>
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
      <div id="sortbar2">
        <button class="sbtn" data-s="artist">Artist</button>
        <button class="sbtn" data-s="bpm">BPM</button>
        <button class="sbtn" data-s="dur">Dur</button>
      </div>
    </div>
    <div id="nlist"></div>
  </nav>

  <div id="gw">
    <div id="gc"></div>
    <div id="stabilizing">
      <span id="stab-txt">Laying out graph&hellip;</span>
      <div id="stab-track"><div id="stab-fill"></div></div>
      <span id="stab-pct">0 / __N_NODES__</span>
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
      <div class="ds" id="s-prev" style="display:none">
        <img id="dp-img" src="" alt="">
      </div>
      <div class="ds" id="s-audio" style="display:none">
        <div class="ds-lbl">Playback</div>
        <audio id="dp-audio" controls></audio>
      </div>
      <div class="ds" id="s-meta"><div class="ds-lbl">Properties</div><div id="dp-meta"></div></div>
      <div class="ds" id="s-conn"><div class="ds-lbl">Connections</div><div id="dp-conn"></div></div>
      <div class="ds" id="s-cont"><div class="ds-lbl">Content</div><div id="dp-content"></div></div>
    </div>
  </aside>
</div>

<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.1/anime.min.js"></script>
<script>
const G = __GRAPH_JSON__;
const NM = Object.fromEntries(G.nodes.map(n=>[n.id,n]));

// Node accent palette
const COLORS = {
  folder:'#FF1700', directory:'#FF8E00', document:'#FFE400',
  code:'#06FF00',   image:'#FF8E00',     audio:'#FF1700',
  pdf:'#FFE400',    section:'#555555',
};
const ICONS = {
  folder:'&#128193;', directory:'&#128194;', document:'&#128196;',
  code:'&#128187;',   image:'&#128444;',     audio:'&#127925;',
  pdf:'&#128209;',    section:'&#35;',
};

// ── theme toggle ──────────────────────────────────────────────────────────────
const html = document.documentElement;
let darkMode = true;

function applyTheme(){
  html.setAttribute('data-theme', darkMode ? 'dark' : 'light');
  // update edge colour to match background
  VE.forEach(e=>{
    VE.update({id:e.id, color:{
      color: e.etype==='wikilink'?'#FF8E00':
             e.etype==='related' ?'#FFE400':
             darkMode?'#2e2828':'#D9CFC7',
      highlight:'#FF8E00', hover:'#FF8E00'
    }});
  });
  // update node label colour
  VN.forEach(n=>{
    VN.update({id:n.id, font:{color: darkMode?'#9a9a9a':'#5a5248'}});
  });
  net.redraw();
}

document.getElementById('theme-btn').onclick = ()=>{
  darkMode = !darkMode;
  applyTheme();
};

// ── vis.js dataset ────────────────────────────────────────────────────────────
const VN = new vis.DataSet(G.nodes.map(n=>({
  id:n.id,
  label: n.ntype==='section' ? '' : n.label,
  title: n.label,
  color:{background:n.color, border:n.color,
         highlight:{background:'#fff',border:n.color},
         hover:{background:n.color,border:'#fff'}},
  shape:'dot', size:n.size,
  font:{size:11, color:'#9a9a9a', face:'Inter,Segoe UI,sans-serif'},
})));

const VE = new vis.DataSet(G.edges.map((e,i)=>({
  id:i, from:e.from, to:e.to, arrows:'to',
  etype: e.etype||'default',
  color:{color: e.etype==='wikilink'?'#FF8E00':
                e.etype==='related' ?'#FFE400':'#2e2828',
         highlight:'#FF8E00', hover:'#FF8E00'},
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
    barnesHut:{gravitationalConstant:-11000, centralGravity:.2,
               springLength:140, springConstant:.03, damping:.12, avoidOverlap:.9},
    stabilization:{enabled:true, iterations:500, fit:true},
  },
  interaction:{hover:true, tooltipDelay:60, zoomView:true, hideEdgesOnDrag:false},
  edges:{smooth:{type:'continuous'}},
});

// ── stabilisation progress ────────────────────────────────────────────────────
const stabEl   = document.getElementById('stabilizing');
const stabFill = document.getElementById('stab-fill');
const stabPct  = document.getElementById('stab-pct');

net.on('stabilizationProgress', p=>{
  const pct = Math.round((p.iterations/p.total)*100);
  stabFill.style.width = pct+'%';
  stabPct.textContent  = p.iterations+' / '+p.total;
});

let stabDone = false;
function hideStab(){
  if(stabDone) return; stabDone=true;
  anime({targets:stabEl, opacity:[1,0], duration:500, easing:'easeOutQuad',
         complete:()=>{ stabEl.style.display='none'; }});
}
net.on('stabilized',()=>{
  net.setOptions({physics:{enabled:false}});
  stabFill.style.width='100%';
  stabPct.textContent='Ready';
  setTimeout(hideStab,500);
});
setTimeout(hideStab, 14000);

// ── canvas resize observer ────────────────────────────────────────────────────
new ResizeObserver(()=>{
  net.setSize(gc.offsetWidth+'px', gc.offsetHeight+'px');
  net.redraw();
}).observe(gw);
net.on('dragEnd', ()=> net.redraw());

// ── click ─────────────────────────────────────────────────────────────────────
net.on('click', p=>{ if(p.nodes.length) showDetail(p.nodes[0]); });

// ── navigation ────────────────────────────────────────────────────────────────
function goTo(id){
  net.selectNodes([id]);
  net.focus(id,{scale:Math.max(net.getScale(),.9),
                animation:{duration:380,easingFunction:'easeInOutQuad'}});
  showDetail(id);
}

// ── hamburger ────────────────────────────────────────────────────────────────
const sb = document.getElementById('sb');
let sbOpen = true;
document.getElementById('ham').onclick = ()=>{
  sbOpen = !sbOpen;
  anime({targets:sb, width:sbOpen?248:0, duration:220, easing:'easeInOutQuad',
         complete:()=>{ net.setSize(gc.offsetWidth+'px',gc.offsetHeight+'px'); net.redraw(); }});
};

// ── detail panel ─────────────────────────────────────────────────────────────
const dp = document.getElementById('dp');
let dpOpen = false;

document.getElementById('dp-close').onclick = closeDetail;
document.getElementById('dp-body').addEventListener('click', e=>{
  const t = e.target.closest('[data-go]');
  if(t) goTo(t.dataset.go);
});

function openPanel(){
  if(dpOpen) return; dpOpen=true;
  anime({targets:dp, translateX:['100%','0%'], duration:250, easing:'easeOutQuad',
         complete:()=>{ net.setSize(gc.offsetWidth+'px',gc.offsetHeight+'px'); net.redraw(); }});
}
function closeDetail(){
  if(!dpOpen) return; dpOpen=false;
  anime({targets:dp, translateX:['0%','100%'], duration:200, easing:'easeInQuad',
         complete:()=>{ net.setSize(gc.offsetWidth+'px',gc.offsetHeight+'px'); net.redraw(); }});
  net.unselectAll(); setActive(null);
}

function showDetail(id){
  const n = NM[id]; if(!n) return;
  setActive(id);

  // header
  document.getElementById('dp-icon').innerHTML    = ICONS[n.ntype]||ICONS.document;
  document.getElementById('dp-title').textContent  = n.label;
  const badge = document.getElementById('dp-badge');
  badge.textContent      = n.ntype;
  badge.style.background = (COLORS[n.ntype]||'#FFE400')+'22';
  badge.style.color      = COLORS[n.ntype]||'#FFE400';

  // image preview
  const prevSec = document.getElementById('s-prev');
  const img     = document.getElementById('dp-img');
  if(n.ntype==='image' && n.fpath){
    img.src = 'file:///'+n.fpath.replace(/\\/g,'/');
    prevSec.style.display = '';
  } else {
    prevSec.style.display = 'none';
    img.src = '';
  }

  // audio player
  const audioSec = document.getElementById('s-audio');
  const audioEl  = document.getElementById('dp-audio');
  if(n.ntype==='audio' && n.fpath){
    audioEl.src = 'file:///'+n.fpath.replace(/\\/g,'/');
    audioSec.style.display = '';
  } else {
    audioEl.src = '';
    audioEl.pause && audioEl.pause();
    audioSec.style.display = 'none';
  }

  // properties — meta string + props dict + connections count
  const rows = [];
  if(n.meta) n.meta.split('  ·  ').forEach(p=>{ if(p.trim()) rows.push(['', p.trim()]); });
  if(n.props){
    Object.entries(n.props).forEach(([k,v])=>{
      if(k && v) rows.push([k, String(v)]);
    });
  }
  if(n.fpath) rows.push(['path', n.fpath]);
  rows.push(['links', n.connections+(n.connections===1?' connection':' connections')]);
  if(n.depth) rows.push(['level','H'+n.depth]);
  document.getElementById('dp-meta').innerHTML =
    rows.map(([k,v])=>
      `<div class="dmr"><span class="dmk">${esc(k)}</span>` +
      `<span class="dmv">${esc(String(v))}</span></div>`
    ).join('');

  // connections
  const seen=new Set(), conns=[];
  G.edges.forEach(e=>{
    const other = e.from===id?e.to : e.to===id?e.from:null;
    if(other && !seen.has(other)){ seen.add(other); conns.push({id:other,etype:e.etype||'link'}); }
  });
  const connEl = document.getElementById('dp-conn');
  if(!conns.length){
    connEl.innerHTML='<div style="font-size:11px;color:var(--txt2)">No connections</div>';
  } else {
    connEl.innerHTML = conns.map(c=>{
      const cn=NM[c.id]; if(!cn) return '';
      return `<div class="cl" data-go="${esc(c.id)}">` +
             `<span class="cd" style="background:${COLORS[cn.ntype]||'#FFE400'}"></span>` +
             `<span class="cn">${esc(cn.label)}</span>` +
             `<span class="ct">${esc(c.etype)}</span></div>`;
    }).join('');
  }

  // content
  const sec = document.getElementById('s-cont');
  if(n.content){
    document.getElementById('dp-content').innerHTML = renderContent(n.content);
    sec.style.display='';
  } else {
    sec.style.display='none';
  }

  openPanel();
}

// ── resize handle ─────────────────────────────────────────────────────────────
let resizing=false, rsX=0, rsW=0;
document.getElementById('dp-resize').addEventListener('mousedown', e=>{
  resizing=true; rsX=e.clientX; rsW=dp.offsetWidth;
  document.body.style.cursor='ew-resize'; e.preventDefault();
});
document.addEventListener('mousemove', e=>{
  if(!resizing) return;
  dp.style.width = Math.max(240,Math.min(720,rsW+(rsX-e.clientX)))+'px';
  net.redraw();
});
document.addEventListener('mouseup', ()=>{
  if(resizing){ resizing=false; document.body.style.cursor=''; net.redraw(); }
});

// ── wikilinks ─────────────────────────────────────────────────────────────────
const nameMap={};
G.nodes.forEach(n=>{
  const stem=n.label.replace(/\.[^.]+$/,'').toLowerCase();
  nameMap[stem]=n.id; nameMap[n.label.toLowerCase()]=n.id;
});
function renderContent(txt){
  return esc(txt).replace(/\[\[([^\]]+)\]\]/g,(_,inner)=>{
    const tid=nameMap[inner.toLowerCase()];
    if(tid) return `<span class="wl" data-go="${esc(tid)}">${esc(inner)}</span>`;
    return `<span class="wl-dead">[[${esc(inner)}]]</span>`;
  });
}

// ── sidebar ───────────────────────────────────────────────────────────────────
const ORDER  = ['folder','directory','document','pdf','image','audio','code','section'];
const GLABEL = {folder:'Folders',directory:'Directories',document:'Documents',
                code:'Code',image:'Images',audio:'Audio',pdf:'PDFs',section:'Sections'};
let sortMode='az', query='';

function buildSidebar(){
  const groups={};
  G.nodes.forEach(n=>{ (groups[n.ntype]||(groups[n.ntype]=[])).push(n); });
  Object.values(groups).forEach(a=>sortArr(a));
  const list=document.getElementById('nlist');
  list.innerHTML='';
  ORDER.forEach(type=>{
    const items=groups[type]; if(!items?.length) return;
    const grp=document.createElement('div');
    grp.className='grp';
    const hdr=document.createElement('div');
    hdr.className='grp-hdr';
    hdr.innerHTML=`<span class="ndot" style="background:${COLORS[type]||'#FFE400'};width:6px;height:6px"></span>`+
      `${GLABEL[type]||type}<span class="gcnt">${items.length}</span>`+
      `<span class="garrow">&#9660;</span>`;
    hdr.onclick=()=>grp.classList.toggle('closed');
    const body=document.createElement('div');
    body.className='grp-items';
    items.forEach(n=>{
      const matches=!query||n.label.toLowerCase().includes(query)||
        (n.content||'').toLowerCase().includes(query);
      const el=document.createElement('div');
      el.className='ni'+(matches?'':' dim');
      el.dataset.id=n.id;
      el.innerHTML=`<span class="ndot" style="background:${COLORS[n.ntype]||'#FFE400'}"></span>`+
        `<span class="nlbl">${esc(n.label)}</span>`+
        `<span class="nct">${n.connections}</span>`;
      el.onclick=()=>goTo(n.id);
      body.appendChild(el);
    });
    grp.append(hdr,body);
    list.appendChild(grp);
  });
}

function sortArr(a){
  if(sortMode==='az')    a.sort((x,y)=>x.label.localeCompare(y.label));
  if(sortMode==='za')    a.sort((x,y)=>y.label.localeCompare(x.label));
  if(sortMode==='links') a.sort((x,y)=>y.connections-x.connections);
  if(sortMode==='artist') a.sort((x,y)=>{
    const ax=(x.props&&x.props.artist)||x.label;
    const ay=(y.props&&y.props.artist)||y.label;
    return ax.localeCompare(ay);
  });
  if(sortMode==='bpm') a.sort((x,y)=>{
    const bx=parseFloat((x.props&&x.props.bpm)||0);
    const by=parseFloat((y.props&&y.props.bpm)||0);
    return by-bx;
  });
  if(sortMode==='dur') a.sort((x,y)=>{
    const ds=s=>{const p=String(s).split(':');return p.length===2?parseInt(p[0])*60+parseInt(p[1]):0;};
    return ds((y.props&&y.props.duration)||'0')-ds((x.props&&x.props.duration)||'0');
  });
}
function setActive(id){
  document.querySelectorAll('.ni').forEach(el=>el.classList.toggle('on',el.dataset.id===id));
}
document.querySelectorAll('.sbtn').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.sbtn').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); sortMode=b.dataset.s; buildSidebar();
});
document.getElementById('search').oninput=e=>{
  query=e.target.value.toLowerCase().trim(); buildSidebar();
  if(query){
    net.selectNodes(G.nodes.filter(n=>
      n.label.toLowerCase().includes(query)||(n.content||'').toLowerCase().includes(query)
    ).map(n=>n.id));
  } else { net.unselectAll(); }
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


# ─────────────────────────────────────────────────────────────────────────────
# GUI — Cohere DESIGN.md with light / dark toggle
# ─────────────────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class App:
    def __init__(self):
        global _MODE
        _MODE = "light"

        if _DND:
            self.root = TkinterDnD.Tk()
            self.root.configure(bg=_c("bg"))
        else:
            self.root = ctk.CTk()
            self.root.configure(fg_color=_c("bg"))

        self.root.title("Graphify")
        self.root.geometry("880x700")
        self.root.minsize(720, 580)

        self._input_folder  = tk.StringVar()
        self._output_folder = tk.StringVar(value=str(Path.home() / "graphify_output"))
        self._running       = False

        self._opt_parse   = tk.BooleanVar(value=True)
        self._opt_text    = tk.BooleanVar(value=True)
        self._opt_recurse = tk.BooleanVar(value=True)
        self._opt_code    = tk.BooleanVar(value=False)
        self._opt_media   = tk.BooleanVar(value=True)
        self._opt_related = tk.BooleanVar(value=True)
        self._opt_json    = tk.BooleanVar(value=False)

        self._widgets: list = []   # track theme-sensitive widgets
        self._build_ui()

    # ── theme ─────────────────────────────────────────────────────────────────

    def _toggle_theme(self):
        global _MODE
        _MODE = "dark" if _MODE == "light" else "light"
        ctk.set_appearance_mode(_MODE)
        # re-colour the header (always explicit) and body frame
        self._hdr_frame.configure(fg_color=_c("hdr"))
        self._body_frame.configure(fg_color=_c("bg_alt"))
        # update theme-sensitive registered widgets
        for (wtype, widget, keys) in self._widgets:
            try:
                widget.configure(**{k: _c(v) for k, v in keys.items()})
            except Exception:
                pass
        # drop-zone hover colours depend on mode too — reset to leave state
        self._dz_leave()
        # toggle button label
        self._theme_btn.configure(text="☀" if _MODE == "dark" else "◑")

    def _reg(self, widget, **keys):
        """Register a widget for theme updates. keys: ctk_param -> colour_key."""
        self._widgets.append(("w", widget, keys))
        return widget

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_body()

    def _build_header(self):
        self._hdr_frame = ctk.CTkFrame(
            self.root, fg_color=_c("hdr"), corner_radius=0, height=66)
        self._hdr_frame.pack(fill="x")
        self._hdr_frame.pack_propagate(False)

        inner = ctk.CTkFrame(self._hdr_frame, fg_color="transparent")
        inner.place(relx=0, rely=0.5, anchor="w", x=24)

        # Logo
        ctk.CTkLabel(
            inner, text="\u2735  Graphify",
            font=ctk.CTkFont("Segoe UI", 20, "bold"),
            text_color=N_ORANGE,
        ).pack(side="left", padx=(0, 14))

        # Separator
        ctk.CTkFrame(inner, width=1, height=26, fg_color=_c("hdr_sep")).pack(
            side="left", padx=(0, 14))

        ctk.CTkLabel(
            inner, text="Knowledge Graph Builder",
            font=ctk.CTkFont("Segoe UI", 12),
            text_color=_c("hdr_sub"),
        ).pack(side="left")

        # Theme toggle — top-right
        self._theme_btn = ctk.CTkButton(
            self._hdr_frame,
            text="◑", width=32, height=28,
            fg_color="transparent",
            hover_color=_c("hdr_sep"),
            text_color=_c("hdr_sub"),
            font=ctk.CTkFont("Segoe UI", 14),
            corner_radius=6,
            command=self._toggle_theme,
        )
        self._theme_btn.place(relx=1.0, rely=0.5, anchor="e", x=-16)

    def _build_body(self):
        self._body_frame = ctk.CTkFrame(self.root, fg_color=_c("bg_alt"), corner_radius=0)
        self._body_frame.pack(fill="both", expand=True)

        wrap = ctk.CTkFrame(self._body_frame, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=22, pady=18)

        left = ctk.CTkFrame(wrap, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, 14))

        right = self._reg(
            ctk.CTkFrame(
                wrap, corner_radius=22,
                fg_color=_c("surface"),
                border_width=1, border_color=_c("border_lt"),
                width=238,
            ),
            fg_color="surface", border_color="border_lt",
        )
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        self._build_drop_zone(left)
        self._build_output_row(left)
        self._divider(left)
        self._build_progress_run(left)
        self._build_options(right)

    def _divider(self, parent):
        self._reg(
            ctk.CTkFrame(parent, height=1, fg_color=_c("border_lt")),
            fg_color="border_lt",
        ).pack(fill="x", pady=10)

    def _section_label(self, parent, text: str):
        lbl = ctk.CTkLabel(
            parent, text=text.upper(),
            font=ctk.CTkFont("Courier New", 10),
            text_color=_c("muted"),
        )
        self._reg(lbl, text_color="muted")
        lbl.pack(anchor="w", pady=(0, 5))

    def _build_drop_zone(self, parent):
        self._dz = self._reg(
            ctk.CTkFrame(
                parent, corner_radius=22,
                fg_color=_c("surface"),
                border_width=1, border_color=_c("border_md"),
                height=168,
            ),
            fg_color="surface", border_color="border_md",
        )
        self._dz.pack(fill="x", pady=(0, 10))
        self._dz.pack_propagate(False)

        self._dz_icon = ctk.CTkLabel(
            self._dz, text="📂", font=ctk.CTkFont(size=36))
        self._dz_icon.pack(pady=(20, 3))

        hint = "Drop a folder here  —  or click to browse" if _DND else "Click to browse"
        self._dz_hint = ctk.CTkLabel(
            self._dz, text=hint,
            font=ctk.CTkFont("Segoe UI", 12), text_color=_c("muted"))
        self._reg(self._dz_hint, text_color="muted")
        self._dz_hint.pack()

        self._dz_path = ctk.CTkLabel(
            self._dz, text="",
            font=ctk.CTkFont("Courier New", 10), text_color=N_ORANGE)
        self._dz_path.pack(pady=(3, 0))

        for w in (self._dz, self._dz_icon, self._dz_hint, self._dz_path):
            w.bind("<Button-1>", lambda _e: self._browse_input())
            w.bind("<Enter>", self._dz_enter)
            w.bind("<Leave>", self._dz_leave)

        if _DND:
            self._dz.drop_target_register(TkinterDnD.DND_FILES)
            self._dz.dnd_bind("<<Drop>>", self._on_drop)

    def _dz_enter(self, _e=None):
        self._dz.configure(border_color=N_ORANGE, fg_color=_c("dz_hover"))

    def _dz_leave(self, _e=None):
        self._dz.configure(border_color=_c("border_md"), fg_color=_c("surface"))

    def _build_output_row(self, parent):
        card = self._reg(
            ctk.CTkFrame(parent, corner_radius=22,
                         fg_color=_c("surface"),
                         border_width=1, border_color=_c("border_lt")),
            fg_color="surface", border_color="border_lt",
        )
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)
        self._section_label(inner, "Output folder")
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")

        entry = self._reg(
            ctk.CTkEntry(
                row, textvariable=self._output_folder,
                font=ctk.CTkFont("Segoe UI", 11),
                fg_color=_c("bg_alt"), border_color=_c("border_md"),
                border_width=1, text_color=_c("text"), corner_radius=7,
            ),
            fg_color="bg_alt", border_color="border_md", text_color="text",
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._reg(
            ctk.CTkButton(
                row, text="Browse", width=78,
                fg_color="transparent",
                border_width=1, border_color=_c("border_md"),
                text_color=_c("text"), hover_color=_c("border_lt"),
                font=ctk.CTkFont("Segoe UI", 11), corner_radius=7,
                command=self._browse_output,
            ),
            border_color="border_md", text_color="text", hover_color="border_lt",
        ).pack(side="right")

    def _build_progress_run(self, parent):
        self._status_lbl = self._reg(
            ctk.CTkLabel(parent, text="",
                         font=ctk.CTkFont("Segoe UI", 11), text_color=_c("muted")),
            text_color="muted",
        )
        self._status_lbl.pack(anchor="w", pady=(0, 5))

        self._pbar = ctk.CTkProgressBar(
            parent, height=3, corner_radius=2,
            progress_color=N_ORANGE, fg_color=_c("border_lt"),
        )
        self._reg(self._pbar, fg_color="border_lt")
        self._pbar.pack(fill="x", pady=(0, 14))
        self._pbar.set(0)

        self._run_btn = ctk.CTkButton(
            parent, text="Build Graph",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            height=44, corner_radius=22,
            fg_color=_c("btn"), hover_color=_c("btn_hover"),
            text_color=_c("btn_text"),
            command=self._run,
        )
        self._reg(self._run_btn, fg_color="btn", hover_color="btn_hover", text_color="btn_text")
        self._run_btn.pack(fill="x")

    def _build_options(self, parent):
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=18)
        self._section_label(inner, "Graph Options")

        opts = [
            ("Parse document structure", self._opt_parse,
             "Detect headings & create section nodes"),
            ("Include text previews",    self._opt_text,
             "Store text snippets in nodes"),
            ("Recurse subdirectories",   self._opt_recurse,
             "Walk all nested folders"),
            ("Include code files",       self._opt_code,
             ".py .js .ts .go and similar"),
            ("Include media files",      self._opt_media,
             "Images, audio, and PDFs with metadata"),
            ("Link related content",     self._opt_related,
             "Edges between files sharing keywords"),
            ("Export JSON",              self._opt_json,
             "Save machine-readable .json alongside"),
        ]

        for label, var, tip in opts:
            grp = ctk.CTkFrame(inner, fg_color="transparent")
            grp.pack(fill="x", pady=(0, 9))

            cb = ctk.CTkCheckBox(
                grp, text=label, variable=var,
                font=ctk.CTkFont("Segoe UI", 11),
                text_color=_c("text"),
                fg_color=N_ORANGE, hover_color=N_ORANGE,
                checkmark_color="#000000",
                border_color=_c("border_md"),
                checkbox_width=15, checkbox_height=15, corner_radius=3,
            )
            self._reg(cb, text_color="text", border_color="border_md")
            cb.pack(anchor="w")

            tip_lbl = ctk.CTkLabel(
                grp, text=tip,
                font=ctk.CTkFont("Segoe UI", 9),
                text_color=_c("muted"), wraplength=188, justify="left",
            )
            self._reg(tip_lbl, text_color="muted")
            tip_lbl.pack(anchor="w", padx=(20, 0))

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_drop(self, event):
        path = event.data.strip().strip("{}").strip('"')
        if os.path.isdir(path):
            self._set_input(path)
        else:
            messagebox.showwarning("Not a folder",
                                   f"Please drop a folder:\n{path}", parent=self.root)

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
        self._dz_hint.configure(text=Path(path).name, text_color=_c("text"))
        self._dz_path.configure(text=path)
        self._dz_leave()

    def _set_status(self, text: str):
        self.root.after(0, lambda t=text: self._status_lbl.configure(text=t))

    def _set_progress(self, value: float):
        self.root.after(0, lambda v=value: self._pbar.set(v))

    # ── run ───────────────────────────────────────────────────────────────────

    def _run(self):
        if self._running:
            return
        folder = self._input_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("No folder selected",
                                 "Please select or drop a folder first.", parent=self.root)
            return
        output = self._output_folder.get().strip()
        if not output:
            messagebox.showerror("No output folder",
                                 "Please specify an output folder.", parent=self.root)
            return

        options = {
            "parse_structure": self._opt_parse.get(),
            "include_text":    self._opt_text.get(),
            "recurse":         self._opt_recurse.get(),
            "include_code":    self._opt_code.get(),
            "include_media":   self._opt_media.get(),
            "link_related":    self._opt_related.get(),
            "export_json":     self._opt_json.get(),
        }

        self._running = True
        self._run_btn.configure(text="Processing…", state="disabled",
                                fg_color=_c("muted"))
        self._pbar.set(0)

        def worker():
            try:
                out = build_graph(folder, output, options,
                                  progress_cb=self._set_progress,
                                  status_cb=self._set_status)
                self.root.after(0, lambda f=out: self._on_done(f))
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, out_file: str):
        self._running = False
        self._run_btn.configure(text="Build Graph", state="normal",
                                fg_color=_c("btn"))
        self._set_status(f"Saved → {out_file}")
        if messagebox.askyesno("Graph ready",
                               f"Saved to:\n{out_file}\n\nOpen in browser?",
                               parent=self.root):
            webbrowser.open(Path(out_file).as_uri())

    def _on_error(self, msg: str):
        self._running = False
        self._run_btn.configure(text="Build Graph", state="normal",
                                fg_color=_c("btn"))
        self._set_status(f"Error: {msg}")
        messagebox.showerror("Error", msg, parent=self.root)

    def run(self):
        self.root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().run()
