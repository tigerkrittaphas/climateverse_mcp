"""Assemble a branded ClimateVerse report around agent-supplied content.

The split of responsibility is the whole point of this module:

  the SERVER owns  — logo, masthead, gradient rule, footer, all CSS
  the AGENT owns   — the title, the lead, and the body sections

That split is what keeps every report on-brand. If the calling agent had to
reproduce the stylesheet itself, the identity would drift a little on every
run and `brand/tokens.css` would stop being the source of truth. It also keeps
roughly 10k tokens of CSS and base64 logo out of the model's context, where
they would cost a fortune and teach it nothing.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from importlib import resources

_BRAND = resources.files(__package__) / "brand"


def _asset(name: str) -> str:
    return (_BRAND / name).read_text(encoding="utf-8")


# `data-theme="light"` is stamped explicitly rather than left to the host: a
# report is a document, and its on-screen form should match the PDF a reader
# prints. tokens.css carries no prefers-color-scheme rule, so this also means an
# OS in dark mode cannot silently restyle a published report.
_PAGE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} — ClimateVerse</title>
<style>
{css}
</style>
</head>
<body class="cv-report">
<article class="cv-stack">

<header class="cv-masthead">
  <div class="cv-masthead-bar">
    <img class="cv-logo" src="{logo}" alt="ClimateVerse" />
    <p class="cv-eyebrow">{eyebrow}</p>
  </div>
  <hr class="cv-rule" />
  <div class="cv-stack-xs">
    <h1 class="cv-title">{title}</h1>
{lead}
  </div>
{meta}
</header>

{body}

<footer class="cv-footer">
  <span>{footer_left}</span>
  <span>Generated {generated}</span>
</footer>

</article>
</body>
</html>
"""

# Cheap guards on the self-contained guarantee. These warn rather than reject:
# the agent, not the server, is the author, and a hard failure at the last step
# of a long analysis is worse than a flagged report.
_EXTERNAL_REF = re.compile(r"""<(?:script|link|iframe)\b|(?:src|href)\s*=\s*["']https?://""", re.I)
_KNOWN_TONES = {"neutral", "info", "warning", "danger", "good", "brand"}
_TONE_ATTR = re.compile(r"""data-tone\s*=\s*["']([^"']*)["']""")


def render_report(
    title: str,
    body_html: str,
    lead: str = "",
    doi: str = "",
    meta: list[str] | None = None,
    eyebrow: str = "Exploratory Data Analysis",
    generated: str | None = None,
) -> tuple[str, list[str]]:
    """Wrap `body_html` in the ClimateVerse report shell.

    Returns the complete self-contained HTML document and a list of warnings.
    `title`, `lead`, `meta` and `eyebrow` are plain text and are escaped;
    `body_html` is trusted markup written against the `cv-*` classes.
    """
    warnings: list[str] = []

    if _EXTERNAL_REF.search(body_html):
        warnings.append(
            "body references an external script/stylesheet/URL; the report is no "
            "longer self-contained and may render differently offline or as PDF. "
            "Embed images as data URIs instead."
        )

    for tone in set(_TONE_ATTR.findall(body_html)) - _KNOWN_TONES:
        warnings.append(
            f'unknown data-tone="{tone}" — it will render as the neutral default. '
            f"Valid tones: {', '.join(sorted(_KNOWN_TONES))}."
        )

    if not body_html.strip():
        warnings.append("body_html is empty; the report contains only its masthead.")

    meta_items = [m for m in (meta or []) if str(m).strip()]
    if doi and not any(doi in m for m in meta_items):
        meta_items.insert(0, doi)

    meta_html = ""
    if meta_items:
        spans = "".join(
            f"    <span>{html.escape(str(m))}</span>\n" for m in meta_items
        )
        meta_html = f'  <div class="cv-meta">\n{spans}  </div>'

    lead_html = ""
    if lead.strip():
        lead_html = f'    <p class="cv-lead">{html.escape(lead)}</p>'

    generated = generated or _dt.date.today().isoformat()

    document = _PAGE.format(
        css=_asset("tokens.css") + "\n" + _asset("components.css"),
        logo=_asset("logo.data-uri.txt").strip(),
        title=html.escape(title),
        eyebrow=html.escape(eyebrow),
        lead=lead_html,
        meta=meta_html,
        body=body_html.strip(),
        footer_left=html.escape(f"ClimateVerse · {doi}" if doi else "ClimateVerse"),
        generated=html.escape(generated),
    )
    return document, warnings
