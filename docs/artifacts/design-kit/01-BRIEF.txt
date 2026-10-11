# Brief, the show-detail page

## What it is
A page for a single TV show, reached at `/shows/<slug>/`. It shows the show's own details
and, below them, the shows most similar to it. It is the first place the recommender
becomes visible to a person instead of living in code.

## The goal
Make someone understand, in one glance, *why* two shows are connected. Every other service
recommends by popularity or by "genre." TVLens connects shows through **the specific people
who made them**, cast and crew. That "why," the named shared people, is the differentiator
and must be the most interesting thing on the page. If a visitor only remembers one thing,
it should be "oh, these two shows share these people."

## Sections

### 1. Show header (the hero)
- Backdrop image behind, poster in front.
- Title, first-air year, tagline.
- Genres, network, number of seasons and episodes.
- TMDb rating with vote count (e.g. "8.9 from 17,784 votes").
- Status (Ended, Returning Series, etc.).
- Overview (the synopsis).

### 2. "More shows like this" (the point of the page)
A ranked list of similar shows. For each one:
- Poster, title, year.
- **The people who connect it to the current show, named.** e.g. "Connected through Vince
  Gilligan, Mark Johnson, Giancarlo Esposito, and 75 others." This is the star element,
  give it real visual weight, not fine print.
- A quiet strength cue is fine (a score or a "78 shared people" count), but secondary to
  the names.
- Clicking a similar show opens *its* detail page. The graph is meant to be walked.

### 3. An honesty caption on the list
The ranking has three possible modes (see the recommender). The list should say which one
it used, so it never pretends to be more certain than it is:
- **weighted** (normal): "Ranked by shared cast and crew."
- **estimated**: "Limited episode data, ranked by an estimate."
- **rating**: "No shared-crew signal yet, ranked by rating."

## What NOT to do
- Do not lead with genre badges or a popularity number. Those are the commodity signals
  TVLens is built to move past.
- Do not bury the shared people. They are the reason the product exists.
- No "because it's trending" or engagement-bait framing.

## Notes for later (not this wireframe, just so the layout leaves room)
- Shared-person names will eventually link to a person page.
- A logged-in user will rate the show from this page (half-star, 0.5 to 5.0).
