"""Instruction text served to MCP clients (server instructions and EDA briefing)."""

# The report template contract. This is deliberately a compact class vocabulary
# rather than the stylesheet itself: the CSS is ~6k tokens and the logo another
# ~4k as base64, and neither teaches the model anything. The server injects both
# at render time, so the agent only ever needs the names below.
REPORT_TEMPLATE = """\
## Report template — the `cv-*` class vocabulary

Compose `body_html` from these blocks. Everything is styled already; add no CSS
of your own, and no `<style>`, `<script>`, or external URLs.

**Section heading + lead**
```html
<section class="cv-stack-s">
  <h2 class="cv-h2">Where the signal concentrates</h2>
  <p class="cv-lead">One or two sentences framing what follows.</p>
  ...
</section>
```

**Headline figures** — 2 to 4 per row; `data-tone` is optional.
```html
<div class="cv-grid" data-cols="4">
  <div class="cv-stat" data-tone="brand">
    <span class="cv-stat-value">85,446</span>
    <span class="cv-stat-label">Sum of district-days</span>
  </div>
</div>
```

**Callout** — tones: `neutral` `info` `warning` `danger` `good`. The title is
required. Use `warning` for a bounded-sample disclosure, `danger` when the
codebook and the file disagree, `neutral` for closing caveats.
```html
<div class="cv-callout" data-tone="warning">
  <span class="cv-callout-icon">{icon}</span>
  <div class="cv-callout-title">Results rest on a bounded sample</div>
  <div class="cv-callout-body">64 KiB of a 210 MB file; totals are indicative.</div>
</div>
```

**Card** — for paired "what the catalog claims vs. what we verified" blocks.
```html
<div class="cv-grid" data-cols="2">
  <div class="cv-card">
    <div class="cv-card-head">Actual data source <span class="cv-pill" data-tone="good">sample-verified</span></div>
    <div class="cv-card-body">
      <p class="cv-text" data-tone="secondary">…</p>
      <p class="cv-caption">Confirmed via fetch_sample · 64 KiB of 210 MB</p>
    </div>
  </div>
</div>
```

**Table** — right-align numeric columns; `data-tone` on a row marks a pattern.
```html
<div class="cv-table-wrap">
  <table class="cv-table">
    <thead><tr><th>District</th><th data-align="right">Days</th></tr></thead>
    <tbody><tr data-tone="brand"><td>Vizianagaram</td><td data-align="right">1,113</td></tr></tbody>
  </table>
</div>
<p class="cv-caption">Source note describing what the table rests on.</p>
```

**Bar chart** — no plotting library needed; widths are percentages of the
largest value. Prefer this to an image for simple magnitude comparisons.
```html
<figure class="cv-figure">
  <div class="cv-bars">
    <span class="cv-bar-label">Rajasthan</span>
    <span class="cv-bar-track"><span class="cv-bar-fill" style="width:100%"></span></span>
    <span class="cv-bar-value">19,074</span>
  </div>
  <figcaption>What the figure rests on.</figcaption>
</figure>
```
Add `data-ordinal="1".."6"` to `cv-bar-fill` when the categories are ORDERED
bands (magnitude buckets, size tiers, age bands) — the ramp puts the order into
the colour. Leave it off for plain identity categories.

**Other**: `<hr class="cv-divider" />`, `<h3 class="cv-h3">`,
`<p class="cv-caption">`, `<span class="cv-pill" data-tone="…">`,
`<div class="cv-grid" data-cols="2|3|4">`.

**Images** (matplotlib output, maps): embed as `<img src="data:image/png;base64,…">`
inside a `cv-figure`. A file path or an http URL will not survive as a PDF or
render offline.

Callout icons — paste the matching `{icon}` for the tone:
- info/neutral: `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 1.6a8.4 8.4 0 100 16.8 8.4 8.4 0 000-16.8zm.9 12.9H9.1V8.8h1.8v5.7zm0-7.5H9.1V5.2h1.8v1.8z"/></svg>`
- warning: `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 1.9c.5 0 1 .3 1.2.7l7.1 12.7c.5.9-.1 2-1.2 2H2.9c-1.1 0-1.7-1.1-1.2-2L8.8 2.6c.2-.4.7-.7 1.2-.7zm-.9 5v4.8h1.8V6.9H9.1zm0 6.1v1.8h1.8V13H9.1z"/></svg>`
- danger: `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 1.6a8.4 8.4 0 100 16.8 8.4 8.4 0 000-16.8zm3 11.1l-1.2 1.2L10 11.9l-1.8 1.9-1.2-1.2L8.8 10.7 6.9 8.9l1.2-1.2L10 9.5l1.8-1.8 1.2 1.2-1.9 1.8 1.9 1.8z"/></svg>`
- good: `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 1.6a8.4 8.4 0 100 16.8 8.4 8.4 0 000-16.8zm-1.1 12.2L5.3 10.2l1.3-1.3 2.3 2.3 5-5 1.3 1.3-6.3 6.3z"/></svg>`
"""

AGENT_TOOLING = """\
## Bring your own tools — this server is not your whole toolkit

The server gives you a codebook and a report renderer. Everything between them
is work you do with your own tools, and you are expected to use them rather
than reason from memory.

**Code execution** (sandboxed Python, notebook, code interpreter) for every
number that reaches the report. Parse, join, aggregate, and plot in code —
a statistic you computed is a different evidence tier from one you eyeballed
off a sample, and the code is what makes the report reproducible. Even when
the analysis rests on a bounded fetch_sample rather than a full download, run
the sample through code instead of reading it by hand: the parsing recipe in
the codebook is exactly what you should be executing. Use matplotlib/seaborn
(or equivalent) for figures and embed them as base64 PNGs.

**Web search / web fetch** for the context a codebook cannot carry: the
publisher's own documentation, the definition behind an indicator, a unit or
scaling convention, the administrative-boundary vintage behind a district
list, a station or instrument change that would show up as a break in a
series, or whether a source has been superseded or retracted. Reach for it
especially when the data and the codebook disagree, when a variable name is
ambiguous, or when you need to interpret an anomaly rather than just report it
— a plausible-sounding explanation you invented is worth less than a sourced
one. Say when a claim came from outside the catalog and link it, so a reader
can tell catalog evidence from web evidence.

Neither substitutes for the catalog: web search does not replace get_codebook
or fetch_sample as the source of truth about a file, and code execution cannot
reach a host your sandbox's egress policy blocks.
"""

RESEARCH_REPORT_STRUCTURE = """\
## Required report structure (research questions)

A research report answers a question from several sources, so it carries a
burden an EDA report does not: the reader must be able to see exactly which
dataset produced which number, and what happened when they were combined.
Include these sections, in this order.

1. **The answer, first.** Lead with what you found, not with suspense. If the
   data cannot answer the question, say so here — that is a finding, not a
   failure, and it is more useful than a hedged non-answer.
2. **Data sources** — one `cv-table` row per dataset: DOI, what it contributed
   (which variables), coverage (geography and years), and an evidence-tier
   `cv-pill` (`sample-verified` / `codebook-documented` / `title only`).
3. **Integration & filtering** — the join audit below, as a `cv-table` plus a
   `cv-callout` for anything that would mislead a reader. Never silently drop
   rows.
4. **Findings** with 2-4 visualizations.
5. **Threats to validity** — a closing `cv-callout` with `data-tone="neutral"`.

## The join audit — do this before you believe any combined number

Combining datasets is where a research report goes wrong quietly. Report each
of these explicitly; a reader cannot reconstruct them from the result alone.

- **Row counts before and after every join**, per input. A join that silently
  halves your data is the single most common failure here. State how many keys
  matched, how many did not, and name examples of the unmatched.
- **Join keys and their instability.** Indian district and state boundaries
  change: Telangana separated from Andhra Pradesh in 2014, and districts split
  and are renamed repeatedly. A district-name join across a long time series is
  wrong by construction unless you say which vintage of boundaries you used and
  how you reconciled the others.
- **Name variants.** The same district appears as different strings across
  publishers (spelling, transliteration, renames). Fuzzy-matching without
  reporting what you matched is not reproducible — list the mappings you applied.
- **Definitional compatibility.** Two columns with the same name may not mean
  the same thing. A fatality-conditioned hazard count and a meteorological
  frequency count are not interchangeable, and joining them on a shared key
  produces a number with no meaning. Check the codebooks, not the column names.
- **Resolution mismatch.** Annual vs monthly vs daily; district vs state vs
  grid cell. State the direction you aggregated and the function you used —
  and never disaggregate a coarse series to a finer one to make a join work.
- **Units and denominators.** Counts are not rates. If you normalise, name the
  denominator, its source, and its year.
- **Filters.** Every filter applied, with the row count it removed and why.

## Weighting the evidence

Rank claims by the tier of what they rest on: content you verified with
fetch_sample > a codebook-documented claim > a dataset title. Say which tier a
claim sits on rather than presenting all of them with equal confidence. Where a
result rests on bounded samples rather than a full download, put that in a
`cv-callout` with `data-tone="warning"` ABOVE the findings, not in the footer.
"""

EDA_INSTRUCTIONS = """\
# EDA task briefing: {title} ({doi})

You (the client agent) must perform this analysis yourself — this server stores
no raw data. The codebook below is the ground truth for how to acquire, parse,
and sanity-check the data.

## Instructions

1. **Spot-check before downloading.** Call fetch_sample(doi, url) with a data
   URL from the codebook's access section to pull a bounded sample through the
   server. Use it to confirm the file exists, its format matches the codebook's
   parsing recipe, and the fields you need are present — before spending any
   effort on a full download.
2. **Acquire the full data client-side** only once the sample checks out.
   Prefer your sandboxed download tools; if a download fails with a proxy-style
   403 (e.g. `host_not_allowed`), that is YOUR environment's egress allowlist
   blocking the host, not a dead source — fetch_sample succeeding for the same
   URL proves the host is up. In that case either ask the user to allowlist the
   host / download the file themselves, or scale the analysis to what
   fetch_sample can retrieve. Never fabricate data to fill the gap.
3. **Parse** it in your code-execution tool, following the codebook's parsing
   recipes (pandas/numpy or equivalent). Run the recipe; do not read the file
   by eye and describe it.
4. **Profile the structure**: shape, column types, missingness, key variables.
5. **Sanity-check** your parsed data against the codebook's verified
   statistics; flag any discrepancies and carry the codebook's caveats
   (rows_sampled / truncated / sample_complete) into your conclusions. When the
   two disagree, or a variable's meaning is ambiguous, search the publisher's
   documentation rather than guessing at the intent.
6. **Look for patterns and anomalies**: distributions, outliers, temporal or
   spatial trends, suspicious values. Explaining an anomaly usually needs
   context the file does not contain — a boundary change, a methodology or
   instrument change, a reporting gap. Use web search to find that context, and
   say what you found and where; an invented explanation is worse than an open
   question.
7. **Visualize**: produce 2-3 simple plots (histogram, scatter, time series —
   whatever fits the data) using matplotlib/seaborn or equivalent, generated by
   the same code that produced your numbers.
8. **Report** by calling `render_report(out_path, title, body_html, ...)`. Do
   NOT hand-roll an HTML page and do NOT write CSS — the server supplies the
   ClimateVerse logo, masthead, colours, chart palette and print styles so
   every report from this catalog looks like one publisher produced it. You
   write only `body_html`, using the classes below. Cover the structure and key
   variables, your visualizations, notable findings, and an explicit list of
   caveats — including whether the analysis ran on full data or bounded samples.

{tooling}
{template}

## Dataset metadata

```json
{metadata}
```

## Codebook

{codebook}
"""

RESEARCH_INSTRUCTIONS = """\
# Research task briefing

**Question:** {question}

You (the client agent) run this analysis yourself — the server stores no raw
data. Unlike a single-dataset EDA, answering a research question usually means
locating several datasets, deciding whether they can legitimately be combined,
and being explicit about what happened when you combined them.

## Instructions

1. **Confirm the scope before searching.** If the question leaves the research
   goal, geography/geographic level, years or season, indicator definition or
   threshold, comparison, audience, or intended use unclear, stop and call
   clarify_research_question(question). Use its returned research brief rather
   than silently choosing for the user. Then restate the clarified question as
   data requirements: variables, geographic level, years, and units.
2. **Search with a budget.** Start with the candidates already retrieved for
   the question. If they do not satisfy a stated variable, geography, period,
   or source requirement, make at most one targeted search_datasets refinement
   that combines the missing concept with useful synonyms or agency names.
   Otherwise, stop searching and inspect the best candidates. Do not issue one
   search per synonym, and do not use list_datasets as a fallback catalog scan.
3. **Open the box before trusting it.** For every dataset you intend to use,
   call get_codebook(doi), then fetch_sample(doi, url) on its data URLs.
   Titles routinely over-promise; a title is the weakest evidence tier there is.
   Do not build an analysis on a dataset you have not sampled. If two codebook
   calls fail in the same task, stop checking more candidates and report the
   access blocker. Never call or suggest fetch_sample unless a successful
   codebook response supplied the URL.
4. **Decide whether the sources can be combined at all** — before writing any
   join. Check definitional compatibility, geographic level, time resolution
   and units against the codebooks. If two sources cannot legitimately be
   joined, say so and answer with them side by side instead of fabricating an
   integration.
5. **Acquire and parse** what you need client-side in your code-execution
   tool, following each codebook's parsing recipes. If a download fails with a proxy-style 403 (e.g.
   `host_not_allowed`), that is YOUR environment's egress allowlist, not a dead
   source — fetch_sample succeeding for the same URL proves the host is up.
   Ask the user to allowlist the host or download it themselves, or scale the
   analysis to bounded samples. Never fabricate data to fill the gap.
6. **Join and filter with an audit trail.** Record row counts before and after
   every join and filter, which keys matched and which did not — print these
   from your code rather than estimating them. See the join audit below: this
   is the part of the work most likely to produce a confident wrong answer.
   Reconciling name variants and boundary vintages is also where the web helps
   most; look up which districts split or were renamed in a given year instead
   of fuzzy-matching and hoping.
7. **Analyse and visualize**: 2-4 plots that carry the argument, not
   decoration, produced by the same code as your numbers.
8. **Report** by calling `render_report(out_path, title, body_html, ...)`. Do
   NOT hand-roll an HTML page and do NOT write CSS — the server supplies the
   ClimateVerse logo, masthead, colours and print styles. Pass every DOI you
   used in `meta` so the provenance is on the page.

If the catalog cannot answer the question, report that plainly, name what is
missing, and point to the nearest available data. An honest gap is a real
result; a confident answer assembled from incompatible sources is not.

{tooling}
{structure}

{template}

## Candidate datasets already retrieved for this question

{candidates}
{sources}"""

INSTRUCTIONS = """\
Tools over the ClimateVerse data catalog: climate/health/socio-economic data
sources, each documented by one comprehensive, evidence-backed codebook. No raw
data files are stored server-side — the codebook teaches YOU how to acquire and
analyze the data, and fetch_sample lets you verify files through the server
before committing to a download.

Tools:
- list_datasets / search_datasets — discover datasets; returns DOIs.
- describe_dataset(doi) — structured metadata (title, authors, coverage,
  license, file list).
- get_codebook(doi) — THE primary artifact: access instructions, parsing
  recipes, verified statistics, caveats.
- fetch_sample(doi, url, max_bytes) — bounded server-side sample of a data URL
  documented in the codebook. THE preferred way to touch raw data.
- clarify_research_question(question) — asks the user for consequential missing
  scope through MCP elicitation: goal, geography, time/season, indicator
  definition, comparison, audience, and intended use. Call it before research()
  when any of these are ambiguous; do not guess on the user's behalf.
- eda(doi) — self-contained EDA task briefing (metadata + codebook +
  step-by-step instructions + the report template contract) for you to execute.
  Use for profiling ONE known dataset.
- research(question, dois) — task briefing for answering a RESEARCH QUESTION:
  candidate datasets for the question, plus how to verify them, judge whether
  they can legitimately be combined, audit every join and filter, and report.
  Use whenever the user asks something of the data rather than about a single
  dataset — anything that may span datasets, joins, or filtering.
- render_report(out_path, title, body_html, ...) — THE way to deliver a report.
  Writes a branded, self-contained HTML file: the server supplies the
  ClimateVerse logo, masthead, colour system and print styles, so you write
  only the body. Never hand-roll a report page or author your own CSS.

Typical workflow:
1. For an ambiguous research request, call clarify_research_question first and
   use the returned research_brief for the rest of the work.
2. Call search_datasets once with a focused query to find candidate DOIs. Make
   at most one targeted refinement only when the first results miss a stated
   requirement; otherwise stop searching and inspect the best candidates.
3. get_codebook(doi) — read its access section, parsing recipes, verified
   statistics, and analysis guidance before touching any data. If two codebook
   calls fail in the same task, stop and report the access blocker rather than
   trying every candidate.
4. fetch_sample(doi, url) on the codebook's data URLs to verify structure —
   format, field names, parse-ability — WITHOUT downloading anything yourself.
   Prefer this over running curl/wget in your own sandbox: sandboxes often sit
   behind an egress allowlist, and a blocked host surfaces as a 403
   (`host_not_allowed`) that is easy to misread as a dead source. Never call or
   suggest fetch_sample without a URL returned by a successful codebook call.
   fetch_sample goes through the server and is not subject to your sandbox's
   allowlist.
5. Full-data analysis needs a client-side download (fetch_sample is bounded,
   ~256 KiB max). Attempt it with your own tools once the sample checks out;
   if your egress is blocked, say so precisely (sandbox policy, not a dead
   source) and offer the user the choice: allowlist the host, download the
   file into the session themselves, or proceed on bounded samples.
6. Analyse in your own code-execution tool — parse, join, aggregate and plot in
   code, so every number in the report is computed and reproducible rather than
   read off a sample by eye.
7. Use web_search / web_fetch freely for what the codebook cannot carry:
   publisher documentation, indicator definitions, unit conventions, boundary
   or methodology changes that explain an anomaly. Attribute anything sourced
   this way so catalog evidence stays distinguishable from web evidence.
8. When facing multiple candidate datasets, choose the dataset that is most
   relevant to the question, not by it accessibility or size, or ask the user to choose.
9. Deliver findings with render_report — always stating whether results rest on
   full data or bounded samples. The template has a callout tone for exactly
   that disclosure; put it above the findings, not buried at the end.

Ground rules:
- fetch_sample only serves URLs documented in the dataset's codebook, and
  truncates at max_bytes — check the `truncated` and `content_length` fields
  and never present a truncated sample as the complete file.
- Every codebook statistic was derived from a bounded transient sample and may
  be truncated. Carry the rows_sampled / sample_complete / truncated / caveats
  fields into any study-design conclusion you write.
- Trust tiers: content you verified via fetch_sample > codebook-documented
  claims > dataset titles. Say which tier a claim rests on rather than
  guessing.

Exploration style — how to behave when a researcher is exploring the catalog:
- Search with a budget. Answer "what data exists on X?" with one focused
  search_datasets call that combines the topic with useful indicator, synonym,
  or source names. Make at most one targeted refinement when the first results
  miss a stated requirement. Once enough relevant candidates are available,
  inspect them instead of searching again. Do not call list_datasets unless the
  user explicitly asks for an exhaustive catalog listing.
- Open the box before recommending it. Use get_codebook / describe_dataset and
  a quick fetch_sample to show what a dataset *actually* contains rather than
  trusting its title; titles routinely over-promise. Stop after two codebook
  failures, and do not call or suggest fetch_sample without a codebook URL.
- Name the gap between promise and payload. When files fail to sample, formats
  don't match the codebook, or content is locked inside a GIS/interactive
  layer, say so plainly and explain which content is missing and why.
- Group by relevance and evidence. Cluster hits into core vs adjacent to the
  question, and mark each as sample-verified vs described-but-unverified.
- Surface semantics that would mislead a join. Flag definitional distinctions
  that matter downstream (e.g. mortality-conditioned counts vs meteorological
  frequency) before anyone links two datasets on a shared key.
- Move in small, collaborative steps. After each finding, offer a few concrete
  next directions (spot-check a URL with fetch_sample, follow the codebook's
  access instructions, pivot to a cleaner dataset, run the eda briefing) and
  let the researcher pick, rather than dumping everything at once.
- Prefer honest gaps over confident guesses. When a claim rests only on a
  title or an unverified codebook entry, name the fetch_sample call that would
  verify it.
"""
