# 10. Rating saves in place, and TVLens takes its first script

## Context
Rating a show is the single interaction the whole product depends on. It is what feeds
Top Picks ([#15](https://github.com/patricio-quezada/tvlens/issues/15)), what unlocks
Side Quests ([ADR-09](09-side-quests-cross-genre-edges.md)), and the cold-start data Layer 2
reads ([ADR-08](08-layer2-personalized-reranking.md)). A user who is rating is usually rating
several shows in a sitting.

Two changes reached it in one day.
[#12](https://github.com/patricio-quezada/tvlens/issues/12) fixed how the widget draws: a font fallback clipped the star glyph, and a `:hover` rule that sticks on touchscreens wiped the saved rating to a 35%-alpha preview.
[#18](https://github.com/patricio-quezada/tvlens/issues/18) replaced the ten radios and the
separate Save button with ten submit buttons, so a click on a star POSTs that score directly.
Both were server-rendered and needed no script.

What neither fixed is that the POST is a POST. Rating went out as a plain form submit and came
back as a redirect, which is a fresh navigation, which always lands at the top of the document.
Patricio, over three messages:

> After I click on the rating the entire page reloads. Can we just have the rating lock on the
> page or is that unnecessary?

> the page relaods and then the page reloads back to the "More Shows Like This" section.

> it should either start back to the original position or just not reload at all and still keep
> the rating.

I made two server-side attempts, and both appear below as alternatives, because the second one shipped briefly and the reason it failed is the reason this ADR exists.

**The deciding fact: a browser never tells the server where the page was scrolled to.** There is
no header, no form field, nothing. Restoring the position a user was at therefore requires
recording it on the client first, which is a script. So "put me back where I was" and "do not
reload at all" cost exactly the same thing, and only one of them also removes the flash. Once
that was clear, the choice was not between a script and no script. It was between a script that
reloads and a script that does not.

## Decision
**Rating POSTs over `fetch` and the page does not navigate. The script is progressive
enhancement: with JavaScript off, the widget is exactly what #18 shipped and still works.**

Four parts.

**The endpoint answers two ways.** `rate()` returns JSON when the request carries
`X-Requested-With: fetch`, and redirects as before otherwise. The scale bounds, the half-step
check and `login_required` are untouched and apply on both paths: this is a public endpoint and
the guards were never client-side.

**Django renders the average sentence and returns it as HTML.** Rating a show changes
"★ 4.2 average from 12 ratings on TVLens" directly under the stars, so an in-place update that
only moved the stars would leave a visibly wrong number on screen. That sentence now lives in
`templates/shows/_rate_meta.html`, included by the detail page and re-rendered into the JSON
response. The copy exists once, in Django, and is never rebuilt in JavaScript.

**No flash message on the in-place path.** `messages.success` assumes something is about to
navigate. Queued with nothing to render it, it would surface later on an unrelated page. The
`.your-score` readout beside the stars is the confirmation, and it is more legible than a banner
at the top of a page the user is not looking at.

**Every failure falls back to the plain submit.** No `fetch`, no `FormData`, a non-2xx response,
a network error: the script rebuilds the score as a hidden input and submits the form normally.
`form.submit()` does not carry a submit button's own name and value, which is the one sharp edge here, and the script handles it explicitly. The worst case is the behaviour this ADR replaces.

### What this reverses
The README says "server-rendered with no JS framework", and that is still true. But the codebase
had **zero** JavaScript, and comments in `detail.html` asserted "No JS anywhere in TVLens" as a
property. That claim is now false, and this change corrects those comments.

Worth being precise about what was and was not given up. This is a script, not a framework:
about 45 lines, inline, no dependency, no build step, no bundler, no vendored file. Every page
including this one is still server-rendered, and the detail page is fully functional with
JavaScript disabled. What changed is that TVLens is no longer able to say it has none, and the
next request to "just add a little JS" no longer meets a bright line. That is the real cost, and
it is a cultural one rather than a technical one.

### Alternatives, and why they lost
**A. Keep the reload and land at the top of the page.** The original behaviour. Correct, and it
loses the user's place on every rating. For a repeated action this is the wrong shape however
fast the server is.

**B. Redirect to `#rate`.** Shipped, briefly. The fragment does put the widget on screen, but it
puts it flush against the *top* of the viewport, and the rating panel is short: heading, stars,
one line of meta. Everything below it fills the rest of the screen, and what is below it is
"More shows like this". Patricio read the result as having been thrown into the
recommendations, which is exactly what it looks like. A fix that relocates a problem is not a
fix.

**C. `scroll-margin-top` on the panel.** Also tried: land the panel a third of the way down
instead of at the very top. Better framing, and still an approximation of a position the server
cannot know. The request was to return to the actual position, and this cannot do that at any
value.

**D. Record the scroll offset in a hidden field and restore it after the redirect.** The only
remaining way to satisfy "put me back where I was". It needs JavaScript to read
`window.scrollY`, so it pays this ADR's whole cost, and it *still* reloads the page. Same price,
worse outcome.

**E. Turbo, htmx, or similar.** These solve this properly and generally. They are also a
dependency, a vendored asset or a CDN, and a build decision, taken on behalf of one interaction.
If TVLens later wants this behaviour in several places, this is the alternative to revisit, and
revisit it deliberately rather than arriving at it by accretion.

**F. Rebuild the average sentence in JavaScript.** Avoids the extra template. It also puts
user-facing copy in two languages in two files, where they drift silently. Returning rendered
HTML costs one small partial and keeps one source of truth.

## After Action Review
Pending. Fill this in after using it. Four things worth watching:

- **The home page rows do not update.** Rating from a detail page changes Top Picks and Side
  Quests, but the user is on the detail page and will not see it until they navigate back. That
  is correct and it is also the first thing that will look like a bug if the row is stale in a
  demo. If it grates, the answer is not to widen the script but to decide whether those rows
  should be reachable from here at all.
- **Whether anything ever exercises the fallback.** It is written and tested at the endpoint, but the
  client-side path is not covered by the Django test suite. If TVLens ever grows a browser test,
  this is the first thing it should cover.
- **Whether this stays one script.** The line was "no JS"; the line is now "one script, no
  framework, everything works without it". That is a much softer boundary, and the next feature that would be easier with a little more will test it.
- **The `X-Requested-With` convention.** Chosen because it is one header and needs no content
  negotiation. If a second endpoint ever needs this, decide then whether to keep the header or
  move to `Accept: application/json`, rather than having both.

Tested in `shows/tests.py::RatingTests`: the in-place path answers 200 with the score and the
re-rendered average instead of redirecting, it queues no flash message, and the scale guards
still reject a bad score over the same path. The plain path still redirects to `#rate`.
