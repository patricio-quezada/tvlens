# TVLens demo walkthrough

> **Decisions, 2026-09-20.** The first take (a feature tour, row by row) was
> shot and rejected. The redo follows two rules:
> 1. **Storytelling, not a tour.** The demo follows one person deciding what to
>    watch tonight: what they rated, what the graph found, what they picked.
>    Rows appear only when the story reaches them.
> 2. **The end card shows the URL in large text** (tvlens.org), because nobody
>    can click a link inside a video. Screenshots carry the URL too.
> The captures under `demo/screenshots/` and `demo/video/` are disposable and
> ignored by git; `shoot.py`, `record_walkthrough.py`, and `build_video.sh` are
> the tooling and are tracked.

A script to read while showing the app. Log in as the live user `test`, open the
home page, and go row by row. Numbers below are the local catalog as of this
build: 249 shows, 3,033 seasons, 164,360 episodes, and 2,893 stored connections
between shows. The live user has rated 11 shows.

## What TVLens is, in one line

TVLens recommends TV by who made the shows you already like, not by what is
trending. You rate what you have seen, and it points you at what to watch next
through the people and the taste those ratings reveal.

## The home page, row by row

The page renders five rows. The first two are built from you; the rest widen out.

### 1. Watch next

*"Unwatched shows that share people with what you rated highly."*

This is the row that answers the page's own question. It looks at the shows you
rated 4 stars or higher, walks out along the people who made them (cast and crew),
and surfaces shows you have not seen that share those people. Then it re-ranks
that list toward your taste and drops anything you have already rated.

On this account it leads with Lost, Private Practice, How to Get Away with Murder,
The Good Doctor, ER, and Scandal. That cluster is not an accident: the user rated
Grey's Anatomy and House highly, and those two shows are dense with medical and
Shondaland crews, so the graph reaches their neighbours first.

### 2. Top Picks for test

*"The shows you rated furthest above the crowd."*

This is a mirror. It is the shows *you* rated, but ranked by how far above the
global average you put each one, not by the raw star count. So a show you loved
that most people are lukewarm on rises above a crowd-pleaser you also liked.
Sons of Anarchy, Game of Thrones, House, Bluey, The Wire, Westworld.

### 3. Recently added

The newest shows in the catalog. This one is not personalized. It is the shelf,
the thing every other row is drawn from, shown newest first so the catalog never
looks static.

### 4. Side Quests

*"Off your usual path, through people you already like."*

This is the surprise row, and it is built to be the opposite of Watch Next. It
only offers shows in genres you have *never* rated highly, reached through people
you already like. So it is a stranger with a mutual friend, not more of the same.
Futurama, Person of Interest, Criminal Minds, The Rookie, Psych, House of the
Dragon. It stays locked until you have rated three shows 4 stars or higher,
because there is no "usual path" to step off of before then.

### 5. Browse by genre

Plain genre pills. The star marks the genres you rate highly, and the order is
your learned affinity, not a size ranking. Before you have rated anything the
pills fall back to which genres hold the best-rated shows, never the most popular.

## The detail page: why a show is recommended

Open any show (Game of Thrones is a good one). Under "More shows like this," every
recommendation carries one plain sentence explaining the tie:

- House of the Dragon, 23 shared people: *"Writer George R.R. Martin wrote for
  both, composer Ramin Djawadi scored every episode of both, director Alan Taylor
  directed 10 episodes and executive producer Miguel Sapochnik produced both, with
  19 others."*
- Person of Interest, 1 shared person: *"Composer Ramin Djawadi scored every
  episode of both."*

That is the whole model on the surface. A person who made a lot of a show counts
for a lot; a one-episode guest counts for a sliver. Cast and crew go in the same
list. There is no black box: the reason is right there in the sentence.

## What the recommender is doing under the hood, in plain language

Think of every show as a dot, and draw a line between two shows whenever they
share a person. Make the line thicker when that person made more of both shows.
That web of lines is the whole first layer, and it is precomputed, so a page load
is a lookup, not a calculation.

The second layer is you. Your ratings bend that web toward the genres and shows
you lean into, so two people looking at the same show see it re-ordered for their
own taste. Watch Next and Side Quests are the same web read two different ways:
one follows your taste forward, the other deliberately steps sideways off it.

## Talking points (say these out loud)

1. **Nothing here is a popularity chart.** Every row is built from what you rated
   or who worked on it. "Trending" is not a signal anywhere in this product, on
   purpose. That is the oldest rule in the codebase.

2. **The connections are people.** Two shows are linked because they share cast or
   crew, weighted by how much of each show that person actually made. A series
   lead counts for a whole show; a guest for one episode counts for almost
   nothing.

3. **There are two kinds of recommendation, by design.** Watch Next points you
   forward along your taste. Side Quests points you sideways, into genres you have
   never rated, but through people you already trust. One is comfort, one is
   discovery, and they never overlap.

4. **It explains itself.** Every recommendation says in a sentence why it is
   there: "Ramin Djawadi scored every episode of both." You can argue with it,
   which you cannot do with a black box.
