"""Assert the Layer 1 facts that live in prose, against the real catalog.

ADR-07's After Action Review recorded "Better Call Saul 14.79, The Blacklist
1.09, CSI 0.19 in eighth" as Breaking Bad's ranking. That was measured on the
100-show catalog and went stale silently as the catalog grew: by August 2026,
CSI had left Breaking Bad's top 12 entirely and nobody noticed for months,
because a prose claim in a document cannot fail.

This command is where those claims go to fail loudly instead. It is a
management command rather than a unit test on purpose: every assertion here is
about the REAL catalog, and the test database is empty. Recreating Breaking
Bad's 75 shared people as a fixture would freeze a copy of the data rather than
the data, which is the failure mode it exists to prevent.

Run it after any rebuild:

    python manage.py check_ground_truth

Exits non-zero on the first failed check, so it can gate a rebuild.
"""

from django.core.management.base import BaseCommand, CommandError

from shows.models import Show, SimilarShow


class Command(BaseCommand):
    help = "Verify the Layer 1 ground truth recorded in the ADRs"

    def handle(self, *args, **options):
        failures = []
        notes = []

        def check(label, condition, detail):
            if condition:
                self.stdout.write(self.style.SUCCESS(f"  PASS  {label}"))
            else:
                self.stdout.write(self.style.ERROR(f"  FAIL  {label}: {detail}"))
                failures.append(f"{label}: {detail}")

        def ranking(name):
            show = Show.objects.filter(name=name).first()
            if show is None:
                return None
            return [
                (e.target.name, e.score)
                for e in SimilarShow.objects.filter(source=show).order_by("rank")
            ]

        self.stdout.write("Breaking Bad (ADR-04, ADR-07):")
        bb = ranking("Breaking Bad")
        if bb is None:
            raise CommandError("Breaking Bad is not in the catalog.")
        for i, (n, sc) in enumerate(bb, start=1):
            self.stdout.write(f"     {i:>2}. {n:<34} {sc:.4f}")

        names = [n for n, _ in bb]
        check(
            "Better Call Saul ranks first",
            names[0] == "Better Call Saul",
            f"got {names[0]}",
        )
        # The spinoff shares an entire production. Its lead over the rest is the
        # clearest signal in the catalog, and a reweighting that narrows it to a
        # near-tie has broken something, whatever the ordering says.
        check(
            "Better Call Saul leads the runner-up by at least 5x",
            len(bb) > 1 and bb[0][1] >= 5 * bb[1][1],
            f"{bb[0][1]:.3f} vs {bb[1][1]:.3f}",
        )
        for expected in ("The Blacklist", "Malcolm in the Middle"):
            check(
                f"{expected} is in the top three",
                expected in names[:3],
                f"top three is {names[:3]}",
            )
        # Both rest on one person at a full 1.0 (Dave Porter's score, Bryan
        # Cranston's lead) and are separated only by tail size. Recorded so the
        # margin is visible rather than assumed; it is too small to assert on.
        if {"The Blacklist", "Malcolm in the Middle"} <= set(names):
            scores = dict(bb)
            notes.append(
                "The Blacklist %.4f vs Malcolm in the Middle %.4f, margin %.4f"
                % (
                    scores["The Blacklist"],
                    scores["Malcolm in the Middle"],
                    abs(scores["The Blacklist"] - scores["Malcolm in the Middle"]),
                )
            )
        # Corrects ADR-07's AAR, which recorded CSI eighth on the 100-show
        # catalog. It is not a top-12 neighbour at 464 shows and should not
        # come back; if it does, something has reweighted the thin tail.
        check(
            "CSI is absent, as it has been since the catalog passed 100 shows",
            not any(n.startswith("CSI") for n in names),
            f"CSI reappeared in {names}",
        )

        self.stdout.write("\nFacility credits must not decide a ranking (ADR-01):")
        for source, forbidden, why in [
            ("Miami Vice", "Real Time with Bill Maher", "a marine coordinator"),
            ("Elementary", "Marvel's Daredevil", "a colorist"),
            ("The Mentalist", "Lanterns", "a sound re-recording mixer"),
        ]:
            got = ranking(source)
            if got is None:
                self.stdout.write(f"  SKIP  {source} is not in the catalog")
                continue
            check(
                f"{source} does not lead with {forbidden}",
                got[0][0] != forbidden,
                f"led with {forbidden}, once linked only by {why}",
            )

        self.stdout.write("\nThe ladder must not leak (ADR-05):")
        modes = set(SimilarShow.objects.values_list("mode", flat=True))
        check(
            "every stored source is on the weighted rung",
            modes <= {"weighted"},
            f"found modes {sorted(modes)}",
        )
        # An edge at exactly 0.0 would drop its source a rung, changing the
        # candidate set rather than only the order, which ADR-05 forbids.
        zeros = SimilarShow.objects.filter(score=0.0).count()
        check("no stored edge scored exactly 0.0", zeros == 0, f"{zeros} edges at 0.0")

        self.stdout.write("\nCatalog:")
        self.stdout.write(
            f"  {Show.objects.count()} shows, {SimilarShow.objects.count()} edges, "
            f"{SimilarShow.objects.values('source').distinct().count()} sources"
        )
        for n in notes:
            self.stdout.write(f"  note: {n}")

        if failures:
            raise CommandError(f"{len(failures)} ground-truth check(s) failed.")
        self.stdout.write(self.style.SUCCESS("\nGround truth holds."))
