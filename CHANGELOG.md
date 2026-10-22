# Changelog

What changed in TVLens, newest first. One entry per release, with a version,
date, and short highlights. Sections, as they apply: Highlights, New,
Improved, Infrastructure, Known issues, Incidents.

## v0.3.1: Oct 22, 2026
**New**
- Privacy and Terms pages, linked from a new footer on every page.
- A Credits page, and TMDB's logo and notice in the footer, as TMDB's API terms require.
- Link previews: sharing a TVLens page on X, LinkedIn, Reddit, or in a message now shows a card with the page's title and the TVLens banner.

## v0.3.0: Oct 21, 2026
**Highlights**
- 🏠 **TVLens lives at tvlens.org.** On Day 80 the app moved from app.tvlens.org to the main domain. Old links forward to the same page, and the 80-Day Project stays at tvlens.org/80dp/.

**New**
- This changelog, linked from the Beta badge.

**Known issues**
- If you were signed in on app.tvlens.org, sign in again on tvlens.org.

## v0.2.0: Oct 18, 2026
**Highlights**
- 📥 **Request a show.** When search finds a show TMDb has but TVLens doesn't, signed-in readers can tap "Request it" and it's imported overnight. When it lands, it leads your Recently Added with "You requested this."
- 🔍 **Smarter search.** "baby sitters" finds The Baby-Sitters Club, and "halo paramount" narrows to Halo on Paramount+, with a pill to switch to the other reading.

**New**
- Show requests: up to 20 open per reader, 10 imported a night (oldest first). Long low-vote shows (soaps, news bulletins) are refused.
- Password reset emails, sent from support@tvlens.org.
- Search reads a streaming service name at the start or end of a query, and shows both readings as pills.

**Improved**
- Genre pages hide shows you've already rated, with a link to bring them back showing your rating.
- Genre pages and the recommender's fallback sort by weighted rating, so a 10.0 on three votes no longer tops everyone's list.
- The "On TMDb, but not in TVLens yet" hint and its Request button now appear beside other results, not only when nothing matched. Long titles wrap cleanly, and it says "1 vote", not "1 votes".
- My Ratings drops the "All shows" link that looked like an expand button; genre and tag pages link back to "Home".

**Infrastructure**
- `/healthz` health route, checked by Fly every 30 s.
- `favicon.ico`, `apple-touch-icon.png` and `robots.txt` served at the site root (robots keeps crawlers off account pages).
- Mail over iCloud custom-domain SMTP with STARTTLS.
- CI checks on every push, plus `REVIEW.md` and a repo `CLAUDE.md` with risk tiers for review.
- The nightly job imports requests before the catalog refresh. A failed import no longer skips the refresh, a TMDb timeout just waits for the next night, and one bad show no longer stops the rest.
- Docs stored as Markdown again.
- tvlens.org: the home page links to the live app, and the build log is published through Day 77 ("Fixes as we go").

**Known issues**
- tvlens.org still says "COMING SOON".
- No privacy or terms pages, TMDb attribution, or `sitemap.xml` yet.

## v0.1.1: Oct 16, 2026
**Improved**
- 🔄 The catalog now refreshes itself every night: the 10 stalest shows nightly, plus a sweep for new shows on Sundays. There's also a manual refresh button that can include the new-show sweep.

**Infrastructure**
- Errors are logged to `fly logs`, and a crash now shows a clean error page instead of nothing.
- A nightly GitHub Action wakes the Fly machine to run the refresh.

## v0.1.0 (Beta): Oct 16, 2026
**Highlights**
- 🚀 **TVLens is live** at https://app.tvlens.org as an invite-only beta.
- ⭐ Rate shows in half-stars and get recommendations from the "people graph" (shared cast and crew).
- 🔍 Every pick shows *why*: the people linking it to something you liked.
- 🧭 "Side Quests" go one step outside your usual taste.

**New**
- Public browsing for everyone; sign-up with an invite code.

**Infrastructure**
- First deploy to Fly.io, with HTTPS on app.tvlens.org.

**Known issues**
- First visit after idle waits 1–2 s (scale to zero).
- No privacy or terms pages yet; TMDb attribution missing.
