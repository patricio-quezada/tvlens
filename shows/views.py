"""Shows app views."""

import logging
import time

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import RegistrationForm
from .models import Genre, Rating, Show, ShowTag, Tag
from .personalization import (
    build_profile,
    genre_quality,
    rated_shows,
    rerank,
    side_quests,
    top_picks,
    watch_next,
    without_watched,
)
from .recommenders import (
    compose_callout,
    name_connections,
    role_indexes,
    shared_connections,
    similar_by_cast,
    similar_by_crew,
    stored_similar,
)
from .search import search as run_search
from .tmdb_client import TMDBClient

logger = logging.getLogger(__name__)

# The MovieLens half-star scale, 0.5 to 5.0 (the same scale ADR-08 assumes for
# Layer 2, enforced by Rating's model validators). Every value the widget offers
# and the only values rate() will store. Multiples of 0.5 are exact in binary,
# so equality checks against this set never suffer float drift.
VALID_SCORES = [n / 2 for n in range(1, 11)]

# How many recommendations the show page offers, and how that grows. ADR-07
# stores 12 edges per show and the page used to render all of them, each with
# its own prose callout, which is a very long page and, in Patricio's demo
# note, "too many now (paradox of choice)".
#
# The rungs are 3, 5, 7, 9: Patricio's numbers, from the Scottish Rite. Three
# is what a reader is offered first, because the job of this section is one
# good next thing to watch rather than a list to work through. Each step up is
# asked for explicitly.
#
# The ladder ends at 12, which is not one of those numbers. It is the number of
# edges ADR-07 stores per show, and ending there means a reader who has clicked
# through every rung is never quietly denied the last three. Stopping at nine
# would leave a tail with no route to it, which is the hole capping the page
# opened in the first place.
RECOMMENDATION_STEPS = (3, 5, 7, 9, 12)
DETAIL_RECOMMENDATION_LIMIT = RECOMMENDATION_STEPS[0]
RECOMMENDATION_MAX = RECOMMENDATION_STEPS[-1]


def star_steps(user_rating):
    """The ten half-star submit buttons for the rating widget, high to low.

    The template renders these in DOM order 5.0 -> 0.5 so the pure-CSS widget can
    fill "this star and every lower one" with a sibling selector (see detail.html).
    Each even half-step is a full star, each odd one the left half of the next.

    Each step is its own submit button carrying `name="score"`, so clicking a
    star POSTs that score and rate() needs no client-side help (#18). `chosen`
    marks the persisted rating: with no radio group there is no :checked to
    select on, so the fill starts from a server-rendered class instead.
    """
    steps = []
    for n in range(10, 0, -1):
        value = n / 2
        steps.append(
            {
                "value": value,
                "css_class": "full" if n % 2 == 0 else "half",
                "chosen": user_rating == value,
            }
        )
    return steps


def favorite_genre_ids_for(user, minimum=2):
    """Genres the user rates highly, with enough evidence to mean it.

    A show carries several genres at once, so one 5-star rating used to star
    three of them. Game of Thrones alone marked Action & Adventure, Drama and
    Sci-Fi & Fantasy, which is how a single opinion ended up glowing across
    every page and reading as a filter left switched on.

    Requiring more than one highly rated show per genre is what makes the
    marker mean "you like this" instead of "you rated something once".
    """
    if not user.is_authenticated:
        return set()
    return set(
        Genre.objects.filter(
            shows__ratings__user=user,
            shows__ratings__score__gte=4.0,
        )
        .annotate(rated=Count("shows", distinct=True))
        .filter(rated__gte=minimum)
        .values_list("id", flat=True)
    )


def index(request):
    base_qs = Show.objects.prefetch_related("genres")

    picks: list = []
    favorite_genre_ids: set = set()
    top_picks_title = None

    if request.user.is_authenticated:
        top_picks_title = f"Top Picks for {request.user.username}"
        # The user's rated shows ranked by lift over a global baseline, so the
        # top of the row is genuinely top, not raw stars replayed (#15).
        picks = top_picks(request.user)
        # A favorite genre is one the user has rated >= 4 stars (the same "high"
        # line Layer 2 personalizes from, ADR-08). The template glows these genre
        # pills and cards so the page shows what it thinks the user likes.
        favorite_genre_ids = favorite_genre_ids_for(request.user)

    # One show, one row. The exclusion chain runs Top Picks > Side Quests >
    # Recently Added, and that priority is by how PERSONAL a row is, not by
    # where it sits on the page: Side Quests renders below Recently Added but
    # claims a show first. Deliberate, do not "fix" it to match render order
    # (ADR-09). A show only ever falls down this chain, never up.
    pick_ids = {s.pk for s in picks}
    # Side Quests is gated on the user's own ratings (ADR-09, amended): it is
    # surprise measured against demonstrated taste, so there is no expectation
    # to violate until a user has three shows rated highly, and no global row
    # to show in the meantime. It returns an empty, locked list for everyone
    # below that line. Called outside the is_authenticated block only because
    # it handles the anonymous case itself, in one place.
    quests = side_quests(request.user, exclude_ids=pick_ids)
    quest_ids = {s.pk for s in quests}
    # Watch Next is the page's answer to "what should I watch next" (#24). It
    # RENDERS first, above Top Picks, because Top Picks is a mirror -- shows the
    # reader has already seen -- and leading with a mirror is what left a reader
    # who finished a show and came back here with nothing pointing forward.
    #
    # But it CLAIMS after Side Quests, which reverses the usual "more personal
    # claims first" rule on purpose. Side Quests can only offer shows in genres
    # the reader has never rated highly, so its pool is a handful; Watch Next's
    # is 74 on the real catalog. The shows they compete over are exactly Side
    # Quests' best ones, connected and novel at once. A row that can afford to
    # lose a candidate yields to one that cannot.
    #
    # It handles the anonymous and no-seed cases itself, so it is called outside
    # the is_authenticated block.
    next_up = watch_next(request.user, exclude_ids=pick_ids | quest_ids)
    next_ids = {s.pk for s in next_up}
    recently_added = base_qs.exclude(pk__in=pick_ids | next_ids | quest_ids).order_by(
        "-created_at"
    )[:12]
    # Browse by genre, ordered by the same cold-start ladder the rest of the
    # recommender uses (ADR-05, ADR-08). A user with no ratings has told us
    # nothing, so the row leads with the genres whose shows TMDb rates highest
    # -- quality, never popularity, which is the one thing ADR-05 forbids.
    # Once a user starts rating, their own learned affinity takes over.
    #
    # Both halves already exist in Layer 2 and neither is recomputed here.
    # _catalog_quality_prior() is literally "each genre's mean vote_average
    # minus the catalog mean", which IS the TMDb-rating ordering, and it is
    # what genre_weights holds on its own at cold start.
    #
    # For a user with ratings the sort key is their learned signal alone, not
    # the effective weight: the prior is deliberately scaled to stay meaningful
    # early, so including it would keep a three-rating user looking at a TMDb
    # ordering. Genres they have said nothing about score 0.0 and fall through
    # to the prior, so the untouched tail still sorts by quality rather than
    # arbitrarily. Catalog count and then name break the remaining ties.
    profile = build_profile(request.user)
    learned = {} if profile.is_cold_start else profile.learned_genre_weights
    quality = profile.genre_weights if profile.is_cold_start else genre_quality()
    genres = sorted(
        Genre.objects.annotate(n=Count("shows")).filter(n__gt=0),
        key=lambda g: (
            -learned.get(g.id, 0.0),
            -quality.get(g.id, 0.0),
            -g.n,
            g.name,
        ),
    )
    return render(
        request,
        "shows/index.html",
        {
            "watch_next": next_up,
            "top_picks": picks,
            "top_picks_title": top_picks_title,
            "side_quests": quests,
            "recently_added": recently_added,
            "genres": genres,
            "favorite_genre_ids": favorite_genre_ids,
        },
    )


def genre(request, pk):
    """All shows in one genre, best-rated first. Powers the home 'Browse by genre' pills."""
    genre = get_object_or_404(Genre, pk=pk)
    shows = (
        Show.objects.filter(genres=genre)
        .prefetch_related("genres")
        .order_by("-vote_average", "name")
    )
    favorite_genre_ids: set = set()
    if request.user.is_authenticated:
        favorite_genre_ids = favorite_genre_ids_for(request.user)
    return render(
        request,
        "shows/genre.html",
        {"genre": genre, "shows": shows, "favorite_genre_ids": favorite_genre_ids},
    )


def detail(request, slug):
    """One show, then the shows it connects to through shared people.

    Layer 1 supplies the candidate list (stored_similar reads the same order
    similar_by_people would compute live, ADR-07); Layer 2 then re-ranks that
    list for the signed-in user by their learned genre/tag preferences (ADR-08).
    For every recommendation the view composes one prose sentence naming the
    people who tie it back, cast and crew, ordered by their episode-share
    contribution, and hands the template the honest caption from the mode.
    """
    show = get_object_or_404(Show.objects.prefetch_related("genres", "networks"), slug=slug)
    # Layer 1 order in, personalized order out. Anonymous and ratingless users
    # get the cold-start ordering (Layer 1 under a light quality prior).
    #
    # Then drop what this reader has already seen (#27), and drop it AFTER the
    # re-rank rather than before. rerank derives each candidate's gravity from
    # its position in a list of length n, so removing a row first would shift
    # the survivors ahead of it down by a rank-step and quietly reorder a list
    # that should only have got shorter. Filtering the whole list here, before
    # the ladder slices a rung off it below, is also what backfills the gap:
    # the reader still sees three, the fourth candidate just moves up.
    ranked = without_watched(request.user, rerank(request.user, stored_similar(show)))

    # TVLens's own rating (distinct from the TMDb vote_average in the hero). The
    # widget shows the signed-in user their current score and lets them change it.
    user_rating = None
    if request.user.is_authenticated:
        user_rating = (
            Rating.objects.filter(user=request.user, show=show)
            .values_list("score", flat=True)
            .first()
        )

    # How far up the ladder this request is. ?show=N is a stateless request
    # for more of this same page: nothing is stored, and leaving the URL leaves
    # the expanded view behind. That distinction is the whole of #9, where a
    # preference wearing the costume of persistent state was the bug. The value
    # is off the URL, so it is checked against the ladder rather than trusted;
    # anything else falls back to the opening step.
    try:
        step = int(request.GET.get("show", ""))
    except (TypeError, ValueError):  # fmt: skip
        step = DETAIL_RECOMMENDATION_LIMIT
    if step not in RECOMMENDATION_STEPS:
        step = DETAIL_RECOMMENDATION_LIMIT

    # Counted after the watched filter, so the ladder never offers a rung with
    # nothing behind it: a reader who has seen much of one neighbourhood gets a
    # shorter climb rather than an empty step.
    available = min(len(ranked), RECOMMENDATION_MAX)
    shown = list(ranked)[:step]
    next_step = None
    if step < available:
        next_step = next(n for n in RECOMMENDATION_STEPS if n > step)

    # One bulk index for the source and every candidate on the page, rather
    # than a pair of queries per candidate.
    indexes = role_indexes([show, *shown])
    source_index = indexes[show.id]
    # rerank hangs the reader's profile on the list it returns, and
    # without_watched carries it through. It orders the people named below by
    # the reader's learned cast-versus-crew preference (issue #7); an anonymous
    # visitor has none and gets the unchanged order.
    profile = getattr(ranked, "profile", None)
    recommendations = []
    for candidate in shown:
        connections = shared_connections(show, source_index, candidate, indexes[candidate.id])
        named, others = name_connections(connections, profile=profile)
        callout = compose_callout(show, candidate, connections, named, others, profile=profile)
        recommendations.append({"show": candidate, "callout": callout})

    # The ladder climbs in place. ?show=N still works on its own, so the link
    # is a real URL and the page is reachable at any rung without script; the
    # fetch branch only spares the reader a reload, exactly as the rating
    # widget does (ADR-10).
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "step": step,
                "next_step": next_step,
                "available": available,
                "recs_html": render_to_string(
                    "shows/_recs_list.html",
                    {"recommendations": recommendations},
                    request=request,
                ),
            }
        )

    others_tagged, tag_suggestions = tag_suggestions_for(request.user, show)

    return render(
        request,
        "shows/detail.html",
        {
            "show": show,
            "recommendations": recommendations,
            "recommendation_step": step,
            "next_recommendation_step": next_step,
            "recommendations_available": available,
            "mode": ranked.mode,
            "personalized": ranked.personalized,
            "user_rating": user_rating,
            "star_steps": star_steps(user_rating),
            "average_rating": show.average_rating,
            "rating_count": show.ratings.count(),
            "user_tags": (
                ShowTag.objects.filter(user=request.user, show=show)
                .select_related("tag")
                .order_by("tag__name")
                if request.user.is_authenticated
                else []
            ),
            # Offer the vocabulary that already exists so a second reader does
            # not invent "slowburn" beside someone else's "slow burn".
            "others_tagged": others_tagged,
            "tag_suggestions": tag_suggestions,
        },
    )


def search(request):
    """Catalog search.

    The filters a reader types inside the box (a year, a season number) are
    parsed out by ParsedQuery and echoed back as chips, so nothing about the
    result set is invisible. The advanced fields are a disclosure, closed by
    default: they exist for the person who wants them and cost nothing to the
    person who does not.
    """
    raw = request.GET.get("q", "")

    def num(name, cast):
        value = request.GET.get(name, "").strip()
        try:
            return cast(value) if value else None
        except ValueError:
            return None

    status = request.GET.get("status", "")
    language = request.GET.get("language", "")
    main_cast_only = request.GET.get("main_cast") == "on"

    started = time.perf_counter()
    shows, parsed = run_search(
        raw,
        fuzzy=request.GET.get("exact") != "1",
        status=status,
        min_score=num("min_score", float),
        min_votes=num("min_votes", int),
        language=language,
        main_cast_only=main_cast_only,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    # Only offer filter values the catalog can actually satisfy. A dropdown
    # listing five statuses when three exist invites empty result sets.
    languages = (
        Show.objects.exclude(original_language="")
        .values_list("original_language", flat=True)
        .distinct()
        .order_by("original_language")
    )

    # Only when the catalog comes back empty, and only for a plain title query.
    # A reader who searched a show we do not have should learn that, rather than
    # concluding search is broken. Nothing is ingested here: this is a label,
    # not a fetch. Adding a show is deliberate, via `manage.py ingest_show`.
    elsewhere = []
    if not shows and raw.strip() and not parsed.too_short:
        try:
            have = set(Show.objects.values_list("id", flat=True))
            elsewhere = [
                {
                    "tmdb_id": r["id"],
                    "name": r.get("name") or "",
                    "year": (r.get("first_air_date") or "")[:4],
                    "votes": r.get("vote_count") or 0,
                    "score": r.get("vote_average") or 0,
                    "overview": (r.get("overview") or "")[:180],
                }
                for r in TMDBClient().search_tv(raw)
                if r.get("id") not in have and (r.get("vote_count") or 0) > 0
            ]
        except Exception:
            # TMDb being down must never break catalog search. The reader loses
            # a hint, not the page.
            logger.warning("TMDb fallback search failed for %r", raw, exc_info=True)

    return render(
        request,
        "shows/search.html",
        {
            "q": raw,
            "parsed": parsed,
            "elsewhere": elsewhere,
            # The page already carries a search box front and centre. A second
            # one in the bar is a duplicate control competing with it.
            "hide_nav_search": True,
            "shows": shows,
            "elapsed_ms": elapsed_ms,
            "statuses": (
                Show.objects.exclude(status="")
                .values_list("status", flat=True)
                .distinct()
                .order_by("status")
            ),
            "languages": languages,
            "f_status": status,
            "f_language": language,
            "f_min_score": request.GET.get("min_score", ""),
            "f_min_votes": request.GET.get("min_votes", ""),
            "f_main_cast": main_cast_only,
            "advanced_open": any(
                [
                    status,
                    language,
                    request.GET.get("min_score"),
                    request.GET.get("min_votes"),
                    main_cast_only,
                ]
            ),
        },
    )


# A tag is a handle, not an essay. Long enough for "slow burn", short enough
# that nobody pastes a sentence into the catalog's shared vocabulary.
TAG_MAX_LENGTH = 40


# How many suggestions to offer. Long enough to cover a real vocabulary, short
# enough that the list stays scannable rather than becoming a dictionary.
TAG_SUGGESTION_LIMIT = 60


def tag_suggestions_for(user, show):
    """Tags worth offering while tagging this show, best first.

    Three tiers, and the order is the whole point. What other people called
    *this show* is the strongest suggestion available: it is the closest thing
    to a second opinion the catalog can offer. The vocabulary in general use
    comes next, ranked by how many people reached for it, because a word ten
    readers chose is more likely to be understood than one somebody coined
    once. Everything else is alphabetical filler.

    Only tag names cross the boundary, never who applied them. A ShowTag row
    belongs to one person; the aggregate is what MovieLens calls a genome and
    is the point of a shared vocabulary.

    Tags the reader has already put on this show are excluded, because
    suggesting them offers a no-op.
    """
    mine = (
        set(ShowTag.objects.filter(user=user, show=show).values_list("tag_id", flat=True))
        if user.is_authenticated
        else set()
    )

    on_this_show = list(
        Tag.objects.filter(show_tags__show=show)
        .exclude(id__in=mine)
        .annotate(readers=Count("show_tags__user", distinct=True))
        .order_by("-readers", "name")[:TAG_SUGGESTION_LIMIT]
    )

    seen = {t.id for t in on_this_show} | mine
    in_general_use = list(
        Tag.objects.exclude(id__in=seen)
        .annotate(uses=Count("show_tags"))
        .filter(uses__gt=0)
        .order_by("-uses", "name")[: TAG_SUGGESTION_LIMIT - len(on_this_show)]
    )

    return on_this_show, on_this_show + in_general_use


def _tag_panel(request, show):
    """Re-render the tag panel for a fetch reply.

    The rating widget set this pattern (ADR-10): the plain form POST still
    works with script off, and the fetch branch only spares the reader a
    reload. It spares them more than that here, because the redirect landed on
    an anchor and threw the page a third of the way down.
    """
    others_tagged, _ = tag_suggestions_for(request.user, show)
    return JsonResponse(
        {
            "html": render_to_string(
                "shows/_tags.html",
                {
                    "show": show,
                    "user_tags": ShowTag.objects.filter(user=request.user, show=show)
                    .select_related("tag")
                    .order_by("tag__name"),
                    "others_tagged": others_tagged,
                    "user": request.user,
                },
                request=request,
            )
        }
    )


@login_required
@require_POST
def add_tag(request, slug):
    """Attach one of the reader's own tags to a show.

    Tags are shared vocabulary but private application: the Tag row is reused
    across everyone, the ShowTag row belongs to one person. That is what lets
    two readers agree that "slow burn" means something without either of them
    seeing the other's shelf.
    """
    show = get_object_or_404(Show, slug=slug)
    raw = (request.POST.get("tag") or "").strip()[:TAG_MAX_LENGTH]
    name = " ".join(raw.split())

    if not name:
        return (
            _tag_panel(request, show)
            if request.headers.get("X-Requested-With") == "fetch"
            else redirect("shows:detail", slug=slug)
        )

    key = slugify(name)
    if not key:
        # Nothing survived slugification, so there is no stable handle to store.
        return (
            _tag_panel(request, show)
            if request.headers.get("X-Requested-With") == "fetch"
            else redirect("shows:detail", slug=slug)
        )

    # Match on the slug, not the name: "Slow Burn" and "slow burn" are the same
    # tag, and letting both exist splits the signal personalization reads.
    tag = Tag.objects.filter(slug=key).first()
    if tag is None:
        tag = Tag.objects.create(name=name, slug=key)

    ShowTag.objects.get_or_create(user=request.user, show=show, tag=tag)
    if request.headers.get("X-Requested-With") == "fetch":
        return _tag_panel(request, show)
    return redirect(f"{reverse('shows:detail', args=[slug])}#tags")


@login_required
@require_POST
def remove_tag(request, slug):
    show = get_object_or_404(Show, slug=slug)
    ShowTag.objects.filter(
        user=request.user, show=show, tag__slug=request.POST.get("tag", "")
    ).delete()
    if request.headers.get("X-Requested-With") == "fetch":
        return _tag_panel(request, show)
    return redirect(f"{reverse('shows:detail', args=[slug])}#tags")


def tag(request, slug):
    """Everything one reader has filed under a tag.

    Anonymous readers get the tag's whole population, which is the only honest
    answer when there is nobody to scope it to.
    """
    tag_obj = get_object_or_404(Tag, slug=slug)
    links = ShowTag.objects.filter(tag=tag_obj).select_related("show")
    if request.user.is_authenticated:
        links = links.filter(user=request.user)

    seen, shows = set(), []
    for link in links:
        if link.show_id in seen:
            continue
        seen.add(link.show_id)
        shows.append(link.show)
    shows.sort(key=lambda s: (-(s.vote_average or 0), s.name))

    return render(request, "shows/tag.html", {"tag": tag_obj, "shows": shows})


@login_required
def my_ratings(request):
    """Everything this user has told TVLens, in one place (#11).

    The rating loop had no mirror. A user could rate a show and then had no way
    to see what they had said, change their mind about it, or understand why
    Top Picks looks the way it does. Every rating appears here, including the
    low ones Top Picks filters out, because this page is the user's record
    rather than a recommendation.

    Most recent first, meaning most recently *touched*: re-rating a show moves
    it back to the top, which is the honest reading of "what have I been saying
    lately". The sort is explicit rather than inherited. Rating.Meta already
    orders by -updated_at, but rated_shows() hands back Show objects, and
    Show's own Meta default is -popularity, which ADR-05 forbids ranking by.
    Name breaks ties so two ratings saved in the same instant stay stable.
    """
    shows = rated_shows(request.user)
    shows.sort(key=lambda s: (-s.rated_at.timestamp(), s.name))

    # This page is the user's record of everything they have told TVLens, and a
    # tag is as much a statement as a score. Ordered by how much they leaned on
    # each word, because that is the shape of a vocabulary: the tag applied to
    # nine shows says more about the reader than the one applied to a single
    # show. Ties break alphabetically so the list is stable between visits.
    tags = list(
        Tag.objects.filter(show_tags__user=request.user)
        .annotate(uses=Count("show_tags", distinct=True))
        .order_by("-uses", "name")
    )

    return render(
        request,
        "shows/my_ratings.html",
        {
            "shows": shows,
            "rating_count": len(shows),
            "average_score": (sum(s.user_score for s in shows) / len(shows) if shows else None),
            "tags": tags,
            # Distinct shows, not summed tag uses: a show carrying three tags
            # must count once here or the copy that says "across N shows"
            # lies whenever a reader's own tags overlap on the same show.
            "tagged_count": ShowTag.objects.filter(user=request.user)
            .values("show_id")
            .distinct()
            .count(),
        },
    )


@login_required
@require_POST
def rate(request, slug):
    """Record or update the signed-in user's half-star rating for one show.

    This is how TVLens collects its OWN ratings (the cold-start data Layer 2
    needs, #6), so the stored shape is exactly one per-user score in 0.5 steps.
    One row per (user, show): update_or_create moves the existing row on a
    re-rate instead of piling up duplicates, which Rating's unique_together
    would reject anyway. The widget only ever POSTs a valid half-step, but this
    is a public endpoint, so the 0.5-to-5.0 bounds and the half-step are
    enforced here too rather than trusted from the client.

    The same endpoint also clears a rating, which is what the widget could not
    do: a POST carrying `clear` deletes the row. Both paths answer the same two
    ways, in place over fetch and by redirect without it (ADR-10).
    """
    show = get_object_or_404(Show, slug=slug)

    # Deselection. The widget could change an opinion but never take one back:
    # once a show was rated, every route out of that state stored a different
    # score. Clearing is its own field rather than score=0, because 0 is not on
    # the MovieLens scale and this endpoint has to keep rejecting it --
    # VALID_SCORES is the contract Layer 2 reads (ADR-08) and it is enforced
    # here because the endpoint is public, not because the widget is trusted.
    # Deleting the row is the clear: an absent rating and a rating of nothing
    # are the same fact, and there is only one way to store it.
    if "clear" in request.POST:
        Rating.objects.filter(user=request.user, show=show).delete()
        score = None
    else:
        try:
            score = float(request.POST.get("score", ""))
        except (TypeError, ValueError):  # fmt: skip
            return HttpResponseBadRequest("Rating must be a number.")
        if score not in VALID_SCORES:
            return HttpResponseBadRequest("Rating must be a half-star step between 0.5 and 5.0.")
        Rating.objects.update_or_create(user=request.user, show=show, defaults={"score": score})

    # The widget asked to stay where it is (ADR-10). Answer with the score it
    # should light and the re-rendered average line, so the wording of that
    # sentence lives in one template instead of being rebuilt in JavaScript.
    # No success message on this path: nothing navigates, so the message would
    # sit in the queue and surface later on an unrelated page.
    # `score` is null on the clear path, which is how the script knows to empty
    # the widget rather than light a star. The average line is re-rendered
    # either way: removing a rating moves the average exactly as adding one
    # does, and on the last rating it changes the sentence entirely.
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "score": score,
                "meta_html": render_to_string(
                    "shows/_rate_meta.html",
                    {
                        "average_rating": show.average_rating,
                        "rating_count": show.ratings.count(),
                    },
                    request=request,
                ),
            }
        )

    if score is None:
        messages.success(request, f"Cleared your rating for {show.name}.")
    else:
        messages.success(request, f"You rated {show.name} {score:g} stars.")
    # Back to the stars, not to the top of the page. Rating is a plain POST and
    # redirect (#18), so the response is a fresh navigation and the browser
    # would otherwise land at the top with the widget scrolled out of sight --
    # which reads as "the page reloaded and lost my click" even though the
    # rating saved. The fragment costs nothing and needs no script.
    return redirect(f"{show.get_absolute_url()}#rate")


def similar(request, pk):
    """Shows connected to this one by shared people. Layer 1, all edges."""
    show = get_object_or_404(Show, pk=pk)
    return render(
        request,
        "shows/similar.html",
        {
            "show": show,
            "by_people": stored_similar(show),
            "by_cast": similar_by_cast(show),
            "by_crew": similar_by_crew(show),
        },
    )


def register(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome to TVLens, {user.username}!")
            return redirect("shows:index")
    else:
        form = RegistrationForm()
    return render(request, "registration/register.html", {"form": form})
