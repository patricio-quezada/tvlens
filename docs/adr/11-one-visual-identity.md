---
adr: 11
title: "One visual identity, defined once, taken from the landing page itself"
status: accepted
date: 2026-08-22
tags:
  - adr
relates: []
---
# 11. One visual identity, defined once, taken from the landing page itself

**Ten of eleven pages wore an older brown palette while only the show page carried the
tvlens.org look, so the nav changed color mid-journey. The identity now lives once in
`base.html`, and it was read off the live landing page rather than copied from the one page
that already had it, which caught three drifts, including a color invented from scratch
because the real one was invisible from inside the repo.**

## Context
The nav changed color on the walk from the homepage into a show.

It is defined once, in `base.html`, so that is not a bug in the nav. It is the nav honestly
reporting that the page under it had a different palette. TVLens had two looks:

| | Palette | Type | Grain | Pages |
|---|---|---|---|---|
| `base.html` `:root` | `#1a1814` brown, `#d4a574` tan | `system-ui` | no | 11 |
| `detail.html` `body.detail` | `#08080a` near-black, `#e89b2d` amber | Bebas Neue + DM Sans | yes | 1 |

The show detail page was not the odd one out. It was the only page carrying the look of
`tvlens.org`. Every other page still wore an older warm-brown palette built around Pantone's
Cloud Dancer, and it surfaced while verifying
[#12](https://github.com/patricio-quezada/tvlens/issues/12): the show page had been brought
into line with the landing page and the homepage had not.

The mechanism to fix it already existed, and `detail.html` said so in its own comment:
redefining the shared palette variables on `body.detail` lets the inherited nav and shell adopt
the cinematic look without a second stylesheet. Everything needed was that same move, one level
up.

## Decision
**`base.html` carries the identity. Nothing overrides it.**

**The palette comes from `tvlens.org`, not from `detail.html`.** This is the part worth
recording, because taking the shortcut would have been invisible and wrong.
`detail.html` was a copy of the landing page made by hand, and copies drift. Fetching
the live landing page and reading its stylesheet found three drifts that would have
been promoted to the whole product:

- the top glow is `rgba(232, 155, 45, 0.07)` on the landing page and had become `0.06`
- `--amber-dim: #c27d1a` and `--amber-glow: #f5a623` exist on the landing page and had never
  been carried over. #12 needed a dim amber for the rating widget's hover preview and invented
  `#9a6822` from scratch, because the real one was not visible from inside the repo. It is now
  `--accent-dim`, the landing page's own value.
- `.streak`, a faint horizontal light line, exists on the landing page. `detail.html`'s comment
  says "a warm glow up top, a faint light streak" and only ever defined the glow. The comment
  describes a streak nobody built.

Each `:root` variable carries the landing page's own variable name, so a glance compares the
two the next time the landing page changes.

**What moved up into `base.html`:** the palette, the Google Fonts link, the film grain,
the top glow, and the TV + LENS nav logo. The grain and the glow are `body::before` and
`body::after` rather than elements, so every page gets them without markup of its own,
and `main` and `nav` take `z-index: 1` to sit above the glow.

**What stayed on the detail page:** a narrower column, the hero, the recommendation
list and the rating widget. That is all genuinely detail-only, and `body.detail` now
carries exactly one rule.

**Bebas Neue goes on four things by hand, never by inheritance.** It is a display face, so it
goes on the nav logo, `.page-header h1`, `.row-title` and `.form-card h1`, and nowhere else.
Body copy stays DM Sans. A display font applied by inheritance would have reached form labels
and card metadata, which is how a cinematic look turns into an unreadable one.

**The star glyph became a token.** `--star-font` pins the same symbol stack the rating
widget needed in #12. The favorite-genre pill draws a star too and had the same latent
clipping bug; both now read from one variable.

### This origin serves the fonts, not Google
The ask was for a monospaced font, or one universal across all devices, and then for whichever
option was easier for the reader. Those two sentences point at different answers, so the
reasoning is recorded here.

**"Universal" and "identical" are opposites unless the font ships with the site.** A
system stack is always available and never the same: the same page is SF Mono on a Mac,
Consolas on Windows and Liberation Mono on Linux. The only way to get identical type on
every device is to serve the font.

**I rejected monospace on the reader's behalf.** TVLens exists to explain a
recommendation in a sentence ("nine people worked on both this and Better Call Saul"),
and it renders show overviews, row captions and callout prose. Monospace is
measurably harder to read at length. It would have been a stylistic win paid for by the
reader on every recommendation, which is the wrong trade for a product whose value is
the sentence.

**So: Bebas Neue and DM Sans, self-hosted.** This delivers what "universal" was reaching for
and more. It renders identically everywhere. It works behind a privacy blocker, on a network
that cannot reach Google's font host, and offline. It stops handing every visitor's IP address
to a third party in exchange for a typeface. Both faces carry the SIL Open Font License, which
permits redistribution.

**DM Sans upright is the variable file.** One 61 KB download covers every weight from
100 to 1000. Shipping the three static weights the CSS asks for would have cost 108 KB
and still synthesized 600 and 700, which base.html uses; those are now real. Italic
stays a single static 300, because that is the only italic in the product, and the
variable italic is 61 KB on its own.

Six files, 136 KB in each repo, 89 KB on a first latin-only page load. `latin-ext` is
gated by `unicode-range` and only downloads for a page that actually contains those
characters, which matters because the catalog has foreign-language shows.

### What is deliberately not ported
`.streak` is fixed at 38% of the viewport height. The landing page is one screen tall,
so it reads as a horizon. Every page here scrolls, so a fixed line across the middle
would sit on top of content rather than behind a composition. The identity is the
palette, the type and the grain; the streak is staging for a single-screen page.

### Alternatives, and why they lost
**A. Leave it.** Two looks, and the nav changing color mid-journey. This is the state this
record fixes, and it needs no further argument.

**B. Promote `detail.html`'s tokens instead of reading the landing page.** Faster, and
it would have shipped a copy of a copy, silently locking in all three drifts above,
including a hand-invented amber sitting next to a real one nobody could see.

**C. A second stylesheet for "cinematic" pages.** This is what the original comment
explicitly avoided, and the reason holds: two stylesheets means two places to change a
color and no answer to which one is right.

**D. Keep loading fonts from Google's CDN.** Fewer bytes in the repo and a chance the
visitor already has them cached from another site. It also means the page renders
differently, or not as designed, for anyone whose network or browser blocks that host,
and it reports every visitor to a third party. The landing page and the app both paid
this cost until now.

**E. Monospace system stack for everything.** Zero bytes, works offline, and it suits a
project built in public from a terminal. It is also harder to read, on a product made of
explanatory sentences, and it would have retired the Bebas wordmark. Rejected on the
reader's behalf; see above.

**F. Move the palette but leave type alone.** Half the identity. The landing page's
voice is as much Bebas Neue over DM Sans as it is amber on near-black, and
`system-ui` headings under an amber palette read as a theme applied to someone else's
app.

## After Action Review
Still open. Three things are worth watching:

- **`--error: #e89180` is a salmon inherited from the old palette.** It never sat beside amber
  before, because form errors only appear on pages that were brown. This change does not
  redesign it, and it may clash.
- **The homepage `h1` now says TVLens directly under a nav logo that says TVLens.** The
  duplication predates this change and was easy to miss while the two sat in different faces at
  different sizes. In one identity it is obvious.
- **Contrast on the quieter text.** `--text-secondary: #7a756c` on `#08080a` suited a landing
  page with very little text. It now carries card metadata, form labels and the row captions,
  far more reading than that color was ever meant to carry.
