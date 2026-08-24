# Artifacts

Recovered from the LaCie backup on 2026-08-24, from `2 Desktop/Writing & Projects`.

These are the pictures the decisions came out of. They are not documentation and
nothing references them at runtime; they are here because a record of how
something was built is worth more once the thing has changed enough that you can
no longer see the alternatives that were rejected.

| File | Date | What it preceded |
|---|---|---|
| `2026-08-12-homepage.png` | Aug 12 | The home page before ADR-11 unified the visual identity |
| `2026-08-12-show-page-v1.png` | Aug 12 | The show page before the rating widget (#5) and the genre grid (#9) |
| `2026-08-13-show-page-wireframe.pdf` | Aug 13 | The wireframe pass, printable |
| `wireframes/` | Aug 13 | 21 explorations of the show-detail page |
| `design-kit/` | Aug 12 | The brief and style guide the wireframes were drawn against |

## The wireframes are the interesting part

Twenty-one options across seven questions, and the numbering is the question:

- **1a-1d** how to show the connection between two shows. `1a-connection-ledger`
  is close to what shipped; `1c-pinned-rail` and `1d-people-first` were not taken.
- **2a-2c** how to present the facts panel.
- **3a-3c** how to show the people. `3a-faces-in-row` shipped.
- **4a-4c** where the rating control goes. `4a-rating-under-poster` shipped, and
  ADR-10 later decided how it saves.
- **5a-5c** how the callout explains itself. `5b-callout-sentence` is the
  ancestor of the prose that issue #4 is still refining.
- **6a** everything combined.
- **7a-7b** desktop and mobile.

## design-kit

`01-BRIEF.md` and `02-STYLE.md` were written before the app had a visual
identity of its own. `02-STYLE.md` points at `landing-page.html` as the style
anchor, which is the same instinct ADR-11 later made a rule: the identity is
defined once, taken from the landing page, and everything else follows it.

`03-sample-data.json` is real recommender output from Aug 12, so it also records
what the graph looked like at 100 shows, before the catalog reached 464.
