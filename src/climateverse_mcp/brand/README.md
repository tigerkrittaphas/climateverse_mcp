# ClimateVerse report identity

Branding for the reports produced by the `eda(doi)` tool. That tool hands the
calling agent a briefing and asks for "a concise markdown or HTML report"; today
every agent invents its own layout. This is the shared shell they fill in.

## Files

| Path | Role |
|---|---|
| `tokens.css` | **Source of truth.** Color, type, space, semantic tones, series palette. |
| `components.css` | Component styles, written entirely against tokens. No raw hex. |
| `logo.png` / `logo.data-uri.txt` | The wordmark, plus a base64 form for embedding. |
| `parts/report.html` | The template, as one page. First line is the Design System card marker. |
| `dist/report.html` | Generated, self-contained. **Do not hand-edit.** |
| `build.py` | Inlines CSS + logo into each fragment. |

It is deliberately **one page**, not a catalog of separate component cards: the
deliverable is a whole report, and every component is shown in the position and
the order it actually occupies in one. The palette and type reference lives in
this README rather than in a specimen page.

```bash
python3 brand/build.py
```

## Where the colors came from

Brand hues were **sampled from the wordmark**, not chosen: a warm horizontal
gradient spanning hues 345°–30°.

| Token | Hex |
|---|---|
| `--cv-brand-crimson` | `#E72028` |
| `--cv-brand-red` | `#EA4127` |
| `--cv-brand-orange` | `#EE6925` |
| `--cv-brand-amber` | `#FBAA1A` |

The categorical **series** palette was derived, not picked. All 5040 orderings of
the hue set were enumerated with brand orange fixed at slot 1; only orderings
with no hard failure in *both* light and dark were kept; the winner was chosen
among the top-scoring ties for its opening colors.

- Worst adjacent CVD ΔE **10.4** light / **9.4** dark (≥8 target)
- Worst adjacent normal-vision ΔE **19.6** light / **19.3** dark (≥15 floor)
- All-pairs forms (scatter, bubble, choropleth, small multiples) are capped at
  the **first three slots**, which clear the harder all-pairs test in both modes.
  Past three: fold into "Other" or facet. That is a series cap, not a licence to
  re-pick colors.

Re-run the validator from the `dataviz` skill after **any** palette change:

```bash
node scripts/validate_palette.js "#EE6925,#1baf7a,#2a78d6,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" \
  --mode light --surface "#FFFFFF"
```

Every text/chrome pair in `tokens.css` was contrast-checked; body ink clears
4.5:1 and muted ink clears 3:1 on its own surface in both modes.

### Categorical vs. ordinal — the distinction that gets missed

`--cv-series-*` encodes **identity** (which state, which region). Re-ordering
those categories would not change their meaning.

`--cv-ordinal-*` encodes **order** (magnitude buckets, size tiers, age bands),
where re-ordering *would* change the meaning. It is a single hue with monotone
lightness so the reader sees the order in the color itself. Using categorical
slots for ordered bands is the easy mistake — it spends the identity channel
re-encoding what the bar length already shows.

Both ramps are validated (`--ordinal` for the ramp): monotone lightness,
adjacent ΔL ≥ 0.06, light end 2.15:1 on the light surface / 2.30:1 on dark.

## The one tension worth knowing

**ClimateVerse's brand hues are the same hues a report needs for "warning" and
"danger."** The brand is warm; there is no way around it. It is resolved by
separation of duty rather than by recoloring:

- The **brand gradient is chrome only** — masthead rule, eyebrows, section
  markers. It never appears inside a chart, where it would compete with the
  series palette for meaning.
- **Every tone always ships with an icon and a text label.** Tone color is never
  the only signal. This is why `.cv-callout` has a mandatory icon and title
  rather than optional ones, and why row tone in tables carries a left edge as
  well as a wash.

## Component vocabulary

Deliberately 1:1 with the reference report, so mapping is mechanical:

| Reference | Here |
|---|---|
| `<Stat value label tone>` | `.cv-stat[data-tone]` |
| `<Card><CardHeader trailing>` | `.cv-card` > `.cv-card-head` |
| `<Pill>` | `.cv-pill[data-tone]` |
| `<Callout tone title>` | `.cv-callout[data-tone]` |
| `<Table columnAlign rowTone>` | `.cv-table`, `td[data-align]`, `tr[data-tone]` |
| `<Grid columns={n}>` | `.cv-grid[data-cols="n"]` |
| `<Text tone="secondary">` | `.cv-lead` / `.cv-text[data-tone]` |
| `<Divider>` | `.cv-divider` |

Tones: `neutral` · `info` · `warning` · `danger` · `good` (plus `brand` on stats,
pills and table rows).

## Constraints these files must keep

1. **Self-contained.** No external CSS, fonts, or images. Reports must render
   identically offline, published pages run under a strict CSP, and the PDF path
   has to match the HTML. This is why the type stack is system sans and the logo
   ships as a data URI.
2. **Light is the default; dark is opt-in.** A report is a document, so its
   on-screen form should match the PDF a reader prints or is sent — it stays
   light even when the OS asks for dark. There is deliberately **no
   `prefers-color-scheme` rule**; dark applies only where a host explicitly
   stamps `data-theme="dark"`. The dark tokens are still fully selected and
   validated (series steps re-chosen for the dark band, not inverted), so that
   path looks right wherever it is used.
3. **Print is part of the identity.** `eda()` asks for HTML *and* PDF, so
   `components.css` ships `@page` margins, `print-color-adjust` on every tinted
   surface, and break rules that keep cards and callouts intact.
