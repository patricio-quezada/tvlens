"""Catalog search.

Two rules shape this module, and both come from measurement rather than taste.

**Never OR two fan-out relations into one filter().** Cast and crew are reverse
foreign keys, and each holds far more rows than the catalog has shows -- in the
hundreds of thousands, and growing as the catalog does (238,357 cast rows and
52,602 crew rows behind 249 shows, measured 2026-09-01). Django compiles a
single filter() with both into one SELECT, and SQLite materializes the cross
product before DISTINCT collapses it: measured against a 100-show catalog,
that was 61 million rows for cast crossed with crew, 368 million once genres
and networks join in. Measured, that query does not return a slow answer, it
does not return at all. Two runs were killed at eight seconds and one was
still executing after two minutes, on a query with sixteen real hits.

The same search, split into one query per branch with the ids unioned in
Python, runs in 32 ms and 74 ms worst case. So every branch below is its own
query and nothing is ever ORed across a join.

**No text index, except where ranking needs one.** icontains compiles to LIKE
'%x%', which is unanchored, so SQLite full-scans whatever index sits on the
column. The slowest single scan in the catalog is Person.name, measured at
5.1 ms against 82,763 rows when this was first written; the people table has
since grown past that (131,639, measured 2026-09-01) and there is still no
latency to recover there, so FTS5 on those columns would be complexity
without payoff. Episode synopses are the exception (#29):
that branch reads an FTS5 index, and the reason is bm25 ordering within the
branch rather than speed, which improved from 170 ms to 43 ms on the worst
term as a side effect. See ADR-12.
"""

import re
from difflib import SequenceMatcher, get_close_matches

from django.db import connection
from django.db.models import Q

from .models import Person, Show
from .recommenders import SQLITE_MAX_VARS_SAFE

# Rank buckets. A show's score is the best bucket that matched it, so a title
# hit outranks a show whose fourth-billed actor shares a name with the query.
# The numbers only have to order correctly relative to each other.
W_TITLE = 100
W_PERSON = 60
W_CHARACTER = 50
W_TAXONOMY = 40  # genre, network
W_BLURB = 30  # overview, tagline
W_SEASON = 20
W_EPISODE = 10

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
SEASON_RE = re.compile(r"\bs(?:eason)?\s*(\d{1,2})\b", re.IGNORECASE)
OPERATOR_RE = re.compile(r'([a-zA-Z]+):("[^"]*"|\S+)')
RANGE_RE = re.compile(r"^(\d{4})\s*(?:-|\.\.)\s*(\d{4})$")
COMPARE_RE = re.compile(r"^([<>]=?)\s*([\d.]+)$")

MIN_TERM = 2

# Which branch each text operator scopes to. Aliases exist because people do
# not agree on one word for a thing: actor and cast are the same request.
TEXT_FIELDS = {
    "title": "title",
    "name": "title",
    "actor": "cast",
    "cast": "cast",
    "crew": "crew",
    "director": "crew",
    "writer": "crew",
    "character": "character",
    "role": "character",
    "genre": "genre",
    "network": "network",
    "channel": "network",
    "description": "blurb",
    "desc": "blurb",
    "overview": "blurb",
    "episode": "episode",
    "tag": "tag",
}
VALUE_FIELDS = {"year", "season", "lang", "language", "status", "score", "votes"}


def _unquote(value):
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else value


class ParsedQuery:
    """A raw search string split into free text, field operators, and filters.

    Three things can appear in the box and only one of them is text.

    Operators (`actor:cranston`, `genre:drama`) scope a search to one branch,
    the way Obsidian scopes with `file:` or `tag:`. Several of them AND
    together, because someone typing two is narrowing, not widening.

    Bare filters are Patricio's rule: a year or a season number typed on its
    own filters with no operator and no button. Both are lifted out of the
    string, because leaving them in would run them twice, once as a filter and
    once as text, and "Season 3" as text matches nearly every show in the
    catalog.

    Free text is whatever is left. It ORs across every branch and is ranked.
    """

    def __init__(self, raw):
        self.raw = (raw or "").strip()
        self.fields = []  # [(branch, term)] - ANDed
        self.year = None
        self.year_to = None
        self.season = None
        self.language = ""
        self.status = ""
        self.min_score = None
        self.max_score = None
        self.min_votes = None
        self.unknown = []  # operators nobody recognised, echoed back

        rest = self._take_operators(self.raw)

        # Bare year and bare season, only if an operator did not already set them.
        if self.year is None:
            match = YEAR_RE.search(rest)
            if match:
                self.year = int(match.group(1))
                rest = rest[: match.start()] + rest[match.end() :]
        if self.season is None:
            match = SEASON_RE.search(rest)
            if match:
                self.season = int(match.group(1))
                rest = rest[: match.start()] + rest[match.end() :]

        self.text = " ".join(rest.split())

    def _take_operators(self, raw):
        kept = []
        cursor = 0
        for match in OPERATOR_RE.finditer(raw):
            key = match.group(1).lower()
            value = _unquote(match.group(2))
            if key not in TEXT_FIELDS and key not in VALUE_FIELDS:
                continue  # not an operator, leave it in the text
            kept.append(raw[cursor : match.start()])
            cursor = match.end()
            self._apply(key, value)
        kept.append(raw[cursor:])
        return " ".join("".join(kept).split())

    def _apply(self, key, value):
        if not value:
            return
        if key in TEXT_FIELDS:
            self.fields.append((TEXT_FIELDS[key], value))
            return

        if key == "year":
            span = RANGE_RE.match(value)
            if span:
                self.year, self.year_to = int(span.group(1)), int(span.group(2))
            elif value.isdigit() and len(value) == 4:
                self.year = int(value)
            else:
                self.unknown.append(f"year:{value}")
        elif key == "season":
            if value.isdigit():
                self.season = int(value)
            else:
                self.fields.append(("season_name", value))
        elif key in ("lang", "language"):
            self.language = value.lower()
        elif key == "status":
            self.status = value
        elif key in ("score", "votes"):
            self._apply_number(key, value)

    def _apply_number(self, key, value):
        compare = COMPARE_RE.match(value)
        try:
            if compare:
                sign, number = compare.group(1), float(compare.group(2))
            else:
                sign, number = ">=", float(value)
        except ValueError:
            self.unknown.append(f"{key}:{value}")
            return
        if key == "votes":
            self.min_votes = int(number)
        elif sign.startswith("<"):
            self.max_score = number
        else:
            self.min_score = number

    @property
    def too_short(self):
        """Typed something, but nothing the catalog can act on.

        A single character is not a search: it matches most of the catalog and
        costs the most to find out. Refusing has to be distinguishable from an
        empty box, because the reader typed and deserves to be told why.
        """
        return (
            bool(self.text)
            and not self.searchable_text
            and not (
                self.fields
                or self.year
                or self.season
                or self.language
                or self.status
                or self.min_score is not None
                or self.max_score is not None
                or self.min_votes is not None
            )
        )

    @property
    def is_empty(self):
        return not (
            self.text
            or self.fields
            or self.year
            or self.season
            or self.language
            or self.status
            or self.min_score is not None
            or self.max_score is not None
            or self.min_votes is not None
        )

    @property
    def searchable_text(self):
        """Text worth running against the catalog.

        A single character matches most of the catalog and costs the most, so
        it buys nothing. A bare year is a valid search with no text at all.
        """
        return self.text if len(self.text) >= MIN_TERM else ""


def _word(field, term):
    """Match `term` at a word boundary in `field`, cheaply.

    Plain icontains is a substring match, and substrings lie. "hbo" matched 46
    shows through cast characters, every one of them the word "neighbour".
    "war" matched 883 through "toward" and "warm". A search that answers a
    three letter query with garbage is worse than one that answers nothing.

    Anchoring with \b alone fixes the results and costs 12x: Django implements
    REGEXP on SQLite as a Python callback, so it pays a function call per row,
    92 ms against 7 ms.

    So AND the two. SQLite evaluates the cheap LIKE first and only runs the
    regex on rows that survive it, which is a handful. Measured at 7 to 13 ms,
    the same as the substring match, with the regex's answers. The term still
    matches word *prefixes*, so "break" finds Breaking Bad while "hbo" no
    longer finds a neighbour.
    """
    pattern = r"\b" + re.escape(term)
    return Q(**{f"{field}__icontains": term, f"{field}__iregex": pattern})


def _fts_query(term):
    """Turn a raw user term into an FTS5 prefix query, or None.

    FTS5 MATCH is a query language, not a string match. Double quotes delimit
    phrases, AND, OR and NOT are operators, and a stray hyphen or apostrophe
    is a syntax error rather than a character. A reader typing don't or
    spider-man is not writing that language, so the whole term becomes one
    quoted phrase, internal quotes doubled the way SQL doubles them, safe by
    construction rather than by a blocklist of operators.

    The trailing star matches word prefixes. #29 measured prefix matching as
    identical to the \\b regex it replaces on every term tried, where exact
    matching silently dropped "murderer" and "murders".

    A term the tokenizer keeps nothing of, like a bare * or --, would build a
    query around the empty phrase, which FTS5 rejects. None tells the caller
    there is nothing to run.
    """
    term = term.strip()
    if not any(c.isalnum() for c in term):
        return None
    escaped = term.replace('"', '""')
    return f'"{escaped}"*'


def _episode_fts(term):
    """Shows whose episode synopses match `term`, as {show_id: bm25}.

    Reads the episode_fts index that migration 0010 builds and its triggers
    keep in sync, instead of scanning 164,360 overviews. The win is not the
    43 ms (#29 measured the old scan at 170 ms on its worst term): it is that
    bm25 finally orders this branch by how well the synopses match rather than
    by the shows' crowd score. A show's rank is its best episode's, and lower
    bm25 is better, which is FTS5's convention, not ours.

    bm25 orders shows within this branch only. Weighing it against the other
    branches would overturn ADR-12's branch ordering, and that needs its own
    evidence.
    """
    query = _fts_query(term)
    if query is None:
        return {}
    with connection.cursor() as cursor:
        # rank is FTS5's built-in bm25 column. The bm25() function itself is
        # refused under an aggregate ("unable to use function bm25 in the
        # requested context"), and hiding it in a subquery only survives until
        # the flattener inlines it; the rank column has no such restriction.
        cursor.execute(
            "SELECT show_id, min(rank) AS best"
            " FROM episode_fts WHERE episode_fts MATCH %s"
            " GROUP BY show_id ORDER BY best",
            [query],
        )
        return {show_id: best for show_id, best in cursor.fetchall()}


def _ids(queryset):
    """Ids only. Never .distinct() on a fan-out join: pull ids and let the set
    deduplicate, which avoids SELECT DISTINCT over a multiplied result."""
    return set(queryset.values_list("id", flat=True))


def _shows_via_people(term, relation, extra=None):
    """Find shows whose cast or crew includes a person matching `term`.

    Two steps, not one join. Joining Show to cast to person makes SQLite
    re-evaluate the name predicate once per credit row, against a cast table
    that outnumbers the people table several times over (238,357 cast rows,
    131,639 people, measured 2026-09-01). Scanning people once and then
    looking shows up by an indexed foreign key measured 59 ms against 9 ms for
    the same 26 results, and 64 ms against 25 ms on a name matching 2,646
    people, on the catalog as it stood then.

    The id list is batched under the SQLite variable ceiling for the reason
    ADR-06 records: the bundled build allows 32,766 bindings and an older
    system build allows 999, so a common surname must not decide whether the
    page returns or 500s.
    """
    person_ids = list(Person.objects.filter(_word("name", term)).values_list("id", flat=True))
    if not person_ids:
        return set()
    found = set()
    for start in range(0, len(person_ids), SQLITE_MAX_VARS_SAFE):
        batch = person_ids[start : start + SQLITE_MAX_VARS_SAFE]
        queryset = Show.objects.filter(**{f"{relation}__person_id__in": batch}, **(extra or {}))
        found |= _ids(queryset)
    return found


def _branch(name, term, main_cast_only=False):
    """Show ids matching `term` in one named branch. One query, one relation.

    Never OR two fan-out relations into one filter(). Cast and crew are reverse
    foreign keys, each holding far more rows than the catalog has shows
    (238,357 cast rows and 52,602 crew rows behind 249 shows, measured
    2026-09-01); Django compiles a single filter() holding both into one
    SELECT and SQLite materialises the cross product before DISTINCT
    collapses it. Measured, that query does not return a slow answer, it does
    not return.
    """
    lead = {"cast__order__lt": 10} if main_cast_only else {}
    queries = {
        "title": lambda: Show.objects.filter(_word("name", term) | _word("original_name", term)),
        "cast": lambda: _shows_via_people(term, "cast", lead),
        "crew": lambda: _shows_via_people(term, "crew"),
        "character": lambda: Show.objects.filter(_word("cast__character", term), **lead),
        "genre": lambda: Show.objects.filter(_word("genres__name", term)),
        "network": lambda: Show.objects.filter(_word("networks__name", term)),
        "blurb": lambda: Show.objects.filter(_word("overview", term) | _word("tagline", term)),
        # Boilerplate season names stay searchable at Patricio's call, so
        # "Thousand-Year Blood War" finds Bleach. Ranked low because most of
        # the 141 distinct names read "Season 3".
        "season_name": lambda: Show.objects.filter(_word("seasons__name", term)),
        # The deepest branch, and the one branch on an FTS5 index (#29).
        # Synopsis only: episode titles were cut deliberately, they are
        # short, generic and mostly noise. Returns {show_id: bm25} so the
        # caller can order within the branch by match quality.
        "episode": lambda: _episode_fts(term),
        # A reader's own vocabulary, not TMDb's. Ranked with genre and network
        # because a tag is the same kind of claim about a show.
        "tag": lambda: Show.objects.filter(_word("user_tags__tag__name", term)),
    }
    result = queries[name]()
    # The episode branch hands back {show_id: bm25}, not a queryset.
    return result if isinstance(result, (set, dict)) else _ids(result)


# Rank order for free text. A title hit outranks a show whose fourth-billed
# actor happens to share a name with the query.
BRANCH_WEIGHTS = [
    ("title", W_TITLE),
    ("cast", W_PERSON),
    ("crew", W_PERSON),
    ("character", W_CHARACTER),
    ("genre", W_TAXONOMY),
    ("network", W_TAXONOMY),
    ("tag", W_TAXONOMY),
    ("blurb", W_BLURB),
    ("season_name", W_SEASON),
    ("episode", W_EPISODE),
]


FUZZY_FLOOR = 0.72
FUZZY_MIN_LEN = 4


def suggest(term):
    """Closest real name in the catalog to a term that matched nothing.

    Secondary by design: this only runs after the exact search comes back
    empty, so a spelling that works never pays for it.

    The candidate set is narrowed in SQL first. Scoring "cranson" against
    every person in the catalog (131,639, measured 2026-09-01) costs more than
    the search it is rescuing, so only names with a *word* starting with the
    same letter are considered. That is the trade: a typo in the first letter
    goes uncaught. Typos land mid-word far more often than at the start of one.

    difflib.get_close_matches does the scoring because it screens with two
    cheap upper bounds before computing a real ratio, which is the difference
    between 200 ms and 40 ms over eight thousand names.
    """
    if len(term) < FUZZY_MIN_LEN:
        return None

    first = term[0]
    names = list(
        Show.objects.filter(_word("name", first)).values_list("name", flat=True)[:2000]
    ) + list(Person.objects.filter(_word("name", first)).values_list("name", flat=True)[:8000])

    span = len(term)
    words = {
        piece for name in names for piece in [name, *name.split()] if abs(len(piece) - span) <= 4
    }

    close = get_close_matches(term, list(words), n=6, cutoff=FUZZY_FLOOR)
    if not close:
        return None

    def shared_prefix(candidate):
        count = 0
        for a, b in zip(term.lower(), candidate.lower()):
            if a != b:
                break
            count += 1
        return count

    # get_close_matches ranks by ratio alone, which lets a rare short name beat
    # the obvious one: "gilligen" scores Gillien above Gilligan. A longer shared
    # prefix breaks that tie, because a typist gets the start of a word right.
    return max(
        close,
        key=lambda c: (shared_prefix(c), SequenceMatcher(None, term.lower(), c.lower()).ratio()),
    )


def search(
    raw_query,
    *,
    status="",
    min_score=None,
    min_votes=None,
    language="",
    main_cast_only=False,
    limit=120,
    fuzzy=True,
):
    """Run a catalog search and return (shows, parsed).

    Free text ORs across every branch and is ranked. Field operators AND
    together, because someone typing two of them is narrowing. Explicit
    arguments come from the advanced panel and lose to an operator typed in
    the box, on the principle that what you typed beats what a form remembered.
    """
    parsed = ParsedQuery(raw_query)
    parsed.suggestion = None

    if parsed.is_empty or parsed.too_short:
        return [], parsed

    ranks = {}
    episode_rank = {}  # show_id -> bm25, lower is better. Episode branch only.
    matched = None  # None means unconstrained by any text branch

    term = parsed.searchable_text
    if term:
        for name, weight in BRANCH_WEIGHTS:
            found = _branch(name, term, main_cast_only=main_cast_only)
            if name == "episode":
                episode_rank = found
            for show_id in found:
                if ranks.get(show_id, 0) < weight:
                    ranks[show_id] = weight
        matched = set(ranks)
        if not matched:
            # Nothing matched exactly. Try once more against the closest real
            # name in the catalog, and say so rather than silently swapping
            # the reader's words for our own.
            if fuzzy:
                near = suggest(term)
                if near and near.lower() != term.lower():
                    corrected = raw_query.replace(term, near)
                    shows, reparsed = search(
                        corrected,
                        status=status,
                        min_score=min_score,
                        min_votes=min_votes,
                        language=language,
                        main_cast_only=main_cast_only,
                        limit=limit,
                        fuzzy=False,
                    )
                    if shows:
                        reparsed.suggestion = near
                        reparsed.raw = raw_query
                        return shows, reparsed
            return [], parsed

    # Operators intersect: actor:cranston genre:drama means both, not either.
    for name, value in parsed.fields:
        found = _branch(name, value, main_cast_only=main_cast_only)
        # An episode: operator's bm25 still orders its survivors, unless the
        # free-text pass already ran the branch and holds the ranking.
        if name == "episode" and not episode_rank:
            episode_rank = found
        ids = set(found)
        matched = ids if matched is None else (matched & ids)
        if not matched:
            return [], parsed

    qs = Show.objects.all() if matched is None else Show.objects.filter(pk__in=matched)

    # A year is "was airing then", not "premiered then". Someone typing 2005
    # means a show that was on, and a show with no last_air_date has not ended.
    if parsed.year is not None:
        upper = parsed.year_to or parsed.year
        qs = qs.filter(first_air_date__year__lte=upper).filter(
            Q(last_air_date__isnull=True) | Q(last_air_date__year__gte=parsed.year)
        )
    if parsed.season is not None:
        qs = qs.filter(seasons__season_number=parsed.season)

    status = parsed.status or status
    language = parsed.language or language
    if parsed.min_score is not None:
        min_score = parsed.min_score
    if parsed.min_votes is not None:
        min_votes = parsed.min_votes

    if status:
        qs = qs.filter(status__iexact=status)
    if language:
        qs = qs.filter(original_language__iexact=language)
    if min_score is not None:
        qs = qs.filter(vote_average__gte=min_score)
    if parsed.max_score is not None:
        qs = qs.filter(vote_average__lte=parsed.max_score)
    if min_votes is not None:
        qs = qs.filter(vote_count__gte=min_votes)

    qs = qs.prefetch_related("genres")

    # The season filter joins, so ids can repeat. Dedupe in Python rather than
    # asking SQLite for DISTINCT across a join.
    seen, shows = set(), []
    for show in qs:
        if show.id in seen:
            continue
        seen.add(show.id)
        show.search_rank = ranks.get(show.id, 0)
        shows.append(show)

    # Rank bucket first; inside the episode bucket, bm25 match quality; then
    # the crowd's score. bm25 only reaches shows the episode branch alone
    # found, because letting it reorder higher buckets would re-weight
    # branches against each other, which #29 ruled out of scope. Never
    # popularity: ADR-05 removed that as an ordering for good reason.
    unranked = float("inf")
    shows.sort(
        key=lambda s: (
            -s.search_rank,
            episode_rank.get(s.id, unranked) if s.search_rank <= W_EPISODE else unranked,
            -(s.vote_average or 0),
            s.name,
        )
    )
    return shows[:limit], parsed
