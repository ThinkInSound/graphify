# Changelog

## [Unreleased] — 2026-04-09 / 2026-04-10

All changes made in this session are uncommitted. The diff lives in `app.py` against commit `8c22767 Initial Graphify GUI upload`.

---

### Added — GUI (`app.py`)

#### Desktop application (`App` class)
- Full customtkinter GUI over the existing `graphify` parsing library
- Drag-and-drop folder input via `tkinterdnd2` (graceful fallback to browse button when not installed)
- Output folder selector with text entry and Browse button
- Thread-safe progress bar (`CTkProgressBar`) and status label driven by worker thread callbacks (`root.after`)
- Dark solid pill "Build Graph" CTA button; opens result in default browser on completion
- **Light / dark mode toggle** (`◑` button in header): switches `ctk.set_appearance_mode`, recolours all registered widgets via `_reg()` / `_toggle_theme()`

#### Options panel
- Parse document structure (headings → section nodes)
- Include text previews (store snippets in nodes)
- Recurse subdirectories
- Include code files
- **Include media files** (new — images, audio, PDFs)
- Link related content (keyword-similarity edges)
- Export JSON

#### Launch infrastructure
- `launch.bat` — checks Python in PATH, installs `customtkinter pyvis tkinterdnd2` if missing, delegates to helper
- `_launch_helper.py` — keeps terminal open with animated `[####----]` progress bar until the Graphify window is detected via `ctypes.windll.user32.FindWindowW`; 20-second timeout with error log fallback

---

### Added — Graph processing (`build_graph`, helpers)

#### Media file support
| Type | Extensions | Metadata extracted |
|------|------------|-------------------|
| Image | jpg jpeg png gif webp bmp tiff ico svg | dimensions, colour mode, EXIF (Make/Model/DateTime) via Pillow |
| Audio | mp3 wav flac ogg m4a aac wma opus | duration, bitrate, channels, sample rate, title/artist/album via mutagen |
| PDF | pdf | page count, Title/Author/Subject/Creator, first-page text preview via pypdf |

All three metadata libraries are optional — `app.py` imports them with `try/except` and degrades to basic file-size metadata when not installed.

#### Wikilink detection
- Scans every node's `content` field for `[[target]]` patterns
- Builds `name_map` (stem → node ID, filename → node ID) across all document/code/pdf nodes
- Creates directed `wikilink` edges; skips self-loops and duplicate edges

#### Keyword-similarity edges
- `_find_keyword_links()` extracts 4+ letter words minus a stop-list, counts shared keywords between text file pairs
- Creates `related` edges (dashed in vis.js) for pairs meeting `threshold=4`

#### Node type expansion
New ntypes added alongside existing `folder / directory / document / code / section`:
- `image`, `audio`, `pdf`

Each ntype gets its own entry in `_NODE_STYLE` with a colour from the neon palette.

---

### Changed — HTML output (`_HTML_TEMPLATE`)

#### Architecture
- Replaced pyvis renderer entirely with a custom single-page application
- Three-panel layout: **sidebar** | **graph canvas** | **detail panel**
- Detail panel is `position:fixed` (overlay) — never steals width from the graph canvas

#### Node colour palette (colorhunt `#FF1700 #FF8E00 #FFE400 #06FF00`)
| ntype | colour |
|-------|--------|
| folder | `#FF1700` red |
| directory / image | `#FF8E00` orange |
| document / pdf | `#FFE400` yellow |
| code | `#06FF00` green |
| audio | `#FF1700` red |
| section | `#555555` dim grey |

#### Light / dark toggle in HTML
- `data-theme="dark"` on `<html>` by default
- CSS custom properties (`--gbg`, `--ui`, `--txt`, etc.) swap per theme
- `◑` button in header calls `applyTheme()` which also updates vis.js edge colours and node label colours to stay readable in both modes

#### Graph physics / rendering fixes
- `smooth.type: 'continuous'` (was `dynamic`) — edges remain visible after physics freeze
- `hideEdgesOnDrag: false` — edges never disappear during pan
- `net.on('dragEnd', redraw)` — additional redraw guard
- `ResizeObserver` on `#gw` — canvas fills container after any layout shift
- `openPanel()` / `closeDetail()` both call `net.setSize + net.redraw` in anime.js `complete` callbacks — graph re-fills after panel open/close

#### Stabilisation overlay
- Replaced tiny pill badge with a centred modal card showing:
  - "Laying out graph…" label
  - Live fill-bar driven by `net.on('stabilizationProgress')`
  - Iteration counter (`x / 500`)
- Fades out with anime.js after `stabilized` event; 14-second hard fallback timeout

#### Sidebar
- Hamburger `☰` button (header) animates sidebar width 248 ↔ 0 via anime.js
- Groups: Folders / Directories / Documents / PDFs / Images / Audio / Code / Sections
- Each group collapsible with animated arrow
- Sort buttons: A–Z / Z–A / Links
- Live search — dims non-matching rows and highlights matching nodes in the graph

#### Detail panel
- Slide-in / slide-out animation via anime.js (`translateX`)
- Drag handle on left edge — resize 240–720 px
- **Image preview** (`<img>` tag with `file:///` path) shown for image nodes
- **Properties section** — renders `meta` string + `props` dict from metadata extraction
- **Connections list** — every connected node, clickable to navigate
- **Content area** — raw text with `[[wikilink]]` patterns rendered as clickable spans; unresolved links shown dashed

---

### Fixed

| Bug | Fix |
|-----|-----|
| `rgba()` crash in tkinter | Replaced `rgba(...)` colour strings with hex equivalents (`#3d2060`, `#8b72b8`) |
| Wrong import casing | `import tkinterdnD2` → `import tkinterdnd2 as TkinterDnD` |
| Duplicate `[tool.poetry.dev-dependencies]` | Removed duplicate section from `pyproject.toml` |
| Terminal closes before window opens | `_launch_helper.py` polls `FindWindowW("Graphify")` and keeps terminal alive |
| Graph blank after detail panel closes | `position:fixed` on `#dp` — panel is overlay, not flex sibling |
| Edges disappear on pan | `smooth:continuous` + `hideEdgesOnDrag:false` + `dragEnd` redraw |
| "Laying out" overlay never hides | `stabilizationProgress` event + anime.js fade-out + 14s fallback |
| No sidebar toggle | Hamburger button + anime.js width animation |
| Right panel not resizable | `#dp-resize` mousedown/mousemove drag handle |

---

### Dependencies added

```
customtkinter >= 5.2   # desktop GUI
tkinterdnd2            # drag-and-drop (optional)
Pillow                 # image metadata (optional)
mutagen                # audio metadata (optional)
pypdf                  # PDF metadata + text (optional)
```

vis.js `9.1.9` and anime.js `3.2.1` loaded from CDN in the HTML output.
