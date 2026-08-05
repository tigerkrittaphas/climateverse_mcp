#!/usr/bin/env python3
"""Build self-contained preview pages from the brand sources.

Each file in brand/parts/ is a body fragment whose FIRST line is the Design
System card marker, e.g.

    <!-- @dsCard group="Foundations" -->

The build inlines tokens.css + components.css and the logo data URI into every
fragment, so each page in brand/dist/ stands alone: no external CSS, no network
requests, no font fetches. That is a hard requirement in three places at once —
the Claude Design pane renders each card in isolation, published pages run under
a strict CSP, and the PDF path has to look identical offline.

Previews are generated, never hand-edited: tokens.css stays the single source of
truth and the cards cannot drift away from it.

    python3 brand/build.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "parts"
DIST = ROOT / "dist"

PAGE = """{marker}
<meta charset="utf-8" />
<style>
{css}
/* Preview chrome — not part of the identity; only frames the card. */
body {{ margin: 0; }}
.cv-specimen {{ padding: 24px; background: var(--cv-plane); min-height: 100vh; box-sizing: border-box; }}
.cv-specimen-note {{
  font-family: var(--cv-font); font-size: 11px; text-transform: uppercase;
  letter-spacing: .14em; font-weight: 600; color: var(--cv-ink-muted);
  margin: 0 0 8px;
}}
</style>
{body}
"""


def build() -> int:
    """Render every fragment in parts/ into a standalone page in dist/."""
    css = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("tokens.css", "components.css")
    )
    logo = (ROOT / "logo.data-uri.txt").read_text(encoding="utf-8").strip()

    fragments = sorted(PARTS.glob("*.html"))
    if not fragments:
        print("no fragments in brand/parts/", file=sys.stderr)
        return 1

    DIST.mkdir(exist_ok=True)
    written = []
    for src in fragments:
        text = src.read_text(encoding="utf-8")
        first, _, body = text.partition("\n")
        marker = first.strip()
        if not re.match(r"<!--\s*@dsCard\b", marker):
            print(f"{src.name}: first line must be an @dsCard marker", file=sys.stderr)
            return 1

        out = PAGE.format(marker=marker, css=css, body=body.strip())
        out = out.replace("{{LOGO}}", logo)

        dest = DIST / src.name
        dest.write_text(out, encoding="utf-8")
        written.append((dest, len(out)))

    for dest, size in written:
        print(f"  {dest.relative_to(ROOT.parent)}  {size / 1024:.1f} KiB")
    print(f"built {len(written)} preview(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
