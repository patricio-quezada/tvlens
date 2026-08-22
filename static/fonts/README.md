# Fonts

Served from this origin rather than Google's CDN, so the type renders identically on
every device, works behind a privacy blocker or a network that cannot reach Google's
font host, works offline, and does not report each visitor's IP address to a third
party for a typeface.

| File | Face |
|---|---|
| `dm-sans-var-*.woff2` | DM Sans, upright, **variable** — one file covers weights 100-1000 |
| `dm-sans-300-italic-*.woff2` | DM Sans, italic 300 — the only italic the product uses |
| `bebas-neue-400-*.woff2` | Bebas Neue 400 — the wordmark and display headings |

`-latin` and `-latin-ext` are separate files gated by `unicode-range`; a page only
downloads `latin-ext` if it actually contains those characters, which the TV catalog
sometimes does.

Both families are licensed under the SIL Open Font License 1.1. The licence text ships
alongside the fonts as required: `OFL-BebasNeue.txt` and `OFL-DMSans.txt`.

The identical set is served by the other property. TVLens the app serves them from
`tvlens/static/fonts`; the landing page serves them from `landing-pages/tvlens/fonts`.
If one side changes, change the other.
