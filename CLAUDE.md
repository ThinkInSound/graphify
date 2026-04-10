# CLAUDE.md — Graphify continuation guide

## What this project is

**Graphify** is two things living in one repo:

1. **Core library** (`graphify/` package, `parse.py`) — a Python text-to-directed-graph parser built on NetworkX. Pre-existing; do not modify unless the user asks.
2. **Desktop GUI** (`app.py`) — a customtkinter application that walks a folder, builds a NetworkX DiGraph from its files, and renders a self-contained HTML knowledge-graph viewer. All session work has been in `app.py`.

---

## File map

```
app.py                  — entire GUI + processing + HTML template (≈ 1 400 lines)
launch.bat              — Windows launcher (checks Python, installs deps, runs helper)
_launch_helper.py       — terminal progress bar; waits for GUI window via FindWindowW
DESIGN.md               — Cohere-inspired design reference (read-only; use for visual decisions)
CHANGELOG.md            — session change log
pyproject.toml          — poetry config; already includes customtkinter, pyvis, tkinterdnd2
graphify/               — core parsing library (NetworkX, do not touch unless asked)
```

---

## Architecture of `app.py`

### Processing pipeline (`build_graph`)
```
_collect_files()          walks folder for text/code/media
  ↓
per-file loop             builds NetworkX DiGraph nodes + directory chain
  _image_meta / _audio_meta / _pdf_meta   extract rich metadata
  _parse_markdown_sections                create section child nodes
  ↓
wikilink scan             [[target]] → directed edges (etype="wikilink")
_find_keyword_links       shared-keyword edges (etype="related")
  ↓
_render_html()            serialises graph to JSON, injects into _HTML_TEMPLATE
```

### Node types and neon colours
| ntype | colour | size |
|-------|--------|------|
| folder | `#FF1700` | 28 |
| directory | `#FF8E00` | 20 |
| document | `#FFE400` | 16 |
| code | `#06FF00` | 16 |
| image | `#FF8E00` | 14 |
| audio | `#FF1700` | 14 |
| pdf | `#FFE400` | 14 |
| section | `#555555` | 9 |

Colours defined in `_NODE_STYLE` dict and mirrored in the JS `COLORS` object in `_HTML_TEMPLATE`.

### GUI (`App` class)
- `_MODE` module-level variable (`"light"` / `"dark"`)
- `_c(key)` returns the current-mode colour from `_LIGHT` / `_DARK` dicts
- `_reg(widget, **keys)` registers a widget for automatic recolour on `_toggle_theme()`
- Two-column layout: left (drop zone + output row + progress + run button) / right (options card)
- Header: deep purple `#1e0a3c` band with orange logo and `◑` theme toggle
- All worker-thread callbacks route through `root.after(0, ...)` for thread safety

### HTML output (`_HTML_TEMPLATE`)
- Raw string (no Jinja). Placeholders: `__TITLE__`, `__N_NODES__`, `__N_EDGES__`, `__GRAPH_JSON__`
- `__GRAPH_JSON__` is `json.dumps({"nodes":[...], "edges":[...]})` with `</` escaped to `<\/`
- CDN deps: `vis-network@9.1.9`, `animejs@3.2.1`
- Three panels: `#sb` sidebar (fixed 248 px, animated collapse), `#gw` graph canvas (flex fills remaining), `#dp` detail panel (`position:fixed` right overlay)
- Theme: `<html data-theme="dark">` with CSS custom properties; toggled by `◑` button
- Physics: barnesHut, 500 iterations, frozen after `stabilized` event
- Edge types: `default` (dim), `wikilink` (orange, 2 px), `related` (yellow, dashed)

---

## Design rules (from DESIGN.md)

- **22 px border-radius** on all primary cards — the Cohere signature
- **Pure White** `#ffffff` / **Snow** `#fafafa` surfaces in light mode
- **Interaction Blue** `#1863dc` only on hover/focus — never as a surface colour
- **Deep purple** `#1e0a3c` header band
- No warm colours in the UI chrome; cool grays (`#f2f2f2`, `#d9d9dd`) for borders
- Dark solid pill button for primary CTA
- Graph node colours use the neon palette (`#FF1700 / #FF8E00 / #FFE400 / #06FF00`) — these are the accent, not the UI chrome

---

## Optional dependencies

Install for richer media metadata:
```
pip install Pillow mutagen pypdf
```
All three are imported with `try/except`; app works without them (falls back to file-size only).

---

## Known patterns / gotchas

### Adding a new node type
1. Add extension set constant (e.g. `VIDEO_EXT`)
2. Add metadata extractor `_video_meta(path)` returning `dict`
3. Handle in the `per-file loop` in `build_graph` — set `ntype`, call extractor, build `meta` string
4. Add entry to `_NODE_STYLE` with colour + size
5. Add entry to JS `COLORS` and `ICONS` dicts in `_HTML_TEMPLATE`
6. Add type to `ORDER` and `GLABEL` in the sidebar JS
7. Extend `_collect_files` to include the new extensions

### Modifying the HTML template
- The template is a Python raw string (`r"""..."""`) — backslashes in JS regex (`\\.`, `\\[\\[`) must stay doubled
- Do not use `</script>` literally inside the template; it will break HTML parsing
- `__GRAPH_JSON__` already has `</` escaped to `<\/` before injection

### Thread safety
All UI updates from the worker thread must use `self.root.after(0, lambda: ...)`.

### Theme toggle
`_toggle_theme()` calls `ctk.set_appearance_mode()` then iterates `self._widgets` list. Any new widget that has theme-sensitive colours should be registered with `self._reg(widget, param="colour_key")`.

---

## Suggested next steps (not yet built)

- **Video file support** — ntype `video`, ffprobe/mutagen for duration/resolution
- **Graph persistence** — save/load the JSON so users can reopen without reprocessing
- **Incremental update** — detect new/changed files and patch the graph rather than rebuild
- **Node search filter in the graph** — dim unmatched nodes visually in the canvas (not just sidebar)
- **Export to Obsidian vault** — write `.md` files with `[[wikilinks]]` mirroring the graph edges
- **Cluster view** — vis.js clustering for large graphs (> 200 nodes)
- **Custom edge colours** per user-defined tag / keyword group
- **Packaging** — PyInstaller one-file exe so `launch.bat` is not needed

---

## Running locally

```bat
launch.bat
```
or directly:
```
python app.py
```

Output is a self-contained `.html` file in the chosen output folder. Open in any modern browser — no server needed.
