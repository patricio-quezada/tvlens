"""A static check that every read of a reader's private rows names the reader.

ADR-14 makes Rating, Review, Watchlist, WatchHistory, ShowTag and ShowRequest
private to one reader each, and REVIEW.md rates an unscoped read of them High.
Reviews alone let it through four times (a1fa27f fixed three at once), so
ReaderScopeCheckTests runs this over the app on every push and fails on a new
one. It reads the source with `ast`; nothing is imported or executed.

Three shapes are checked:

1. `<Model>.objects...` on a private model must call `for_reader(user)` or
   `across_readers()` somewhere in the chain. A write that names its user,
   `create`, `bulk_create`, or `get_or_create`/`update_or_create(user=...)`,
   needs neither.
2. A related manager, `show.ratings...`, the same, unless it hangs off a user
   (`user.ratings`, `request.user.show_tags`), which is scoped by construction.
3. A lookup through a private relation, as a keyword (`shows__ratings__score`)
   or a string argument (`Count("show_tags")`), must sit in a query that also
   filters that relation to a user (`shows__ratings__user=...`).

A deliberate cross-reader read that cannot use `across_readers()` (a lookup
from a public model, such as tag-name suggestions) carries the comment
`# reader-scope: ok` on the flagged line or on the first line of its statement.
"""

import ast
import re
from pathlib import Path

PRIVATE_MODELS = {"Rating", "Review", "Watchlist", "WatchHistory", "ShowTag", "ShowRequest"}
# Reverse accessors onto those models, from Show, Episode, Tag and User.
PRIVATE_RELATIONS = {
    "ratings",
    "reviews",
    "watchlist",
    "watchlisted_by",
    "watch_history",
    "watched_by",
    "show_tags",
    "user_tags",
    "show_requests",
}
SCOPES = {"for_reader", "across_readers"}
PRAGMA = "reader-scope: ok"
LOOKUP = re.compile(r"^[a-z_]+$")


def _parents(tree):
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _chain_up(node, parents):
    """From `X.objects`, climb `.method(...)` links: (method names, kwargs per call)."""
    methods, calls = [], []
    while True:
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            methods.append(parent.attr)
        elif isinstance(parent, ast.Call) and parent.func is node:
            calls.append({k.arg for k in parent.keywords if k.arg})
        else:
            return methods, calls
        node = parent


def _spine_kwargs(call):
    """Keyword names on every call of a method chain ending at `call`."""
    names = set()
    node = call
    while True:
        if isinstance(node, ast.Call):
            names |= {k.arg for k in node.keywords if k.arg}
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return names


def _is_user(node):
    """`user`, `u`, `request.user`, `self.user`: an expression that is a reader."""
    if isinstance(node, ast.Name):
        return node.id in {"user", "u", "reader"}
    return isinstance(node, ast.Attribute) and node.attr == "user"


def _private_prefix(lookup):
    """`shows__ratings__score` -> `shows__ratings`; None if no private hop."""
    parts = lookup.split("__")
    for i, part in enumerate(parts):
        if part in PRIVATE_RELATIONS:
            return "__".join(parts[: i + 1])
    return None


def check_source(source, filename="<source>"):
    """Return [(line, message)] for every unscoped private read in `source`."""
    tree = ast.parse(source, filename)
    lines = source.splitlines()
    parents = _parents(tree)
    findings = []

    def waived(node):
        if PRAGMA in lines[node.lineno - 1]:
            return True
        stmt = node
        while stmt is not None and not isinstance(stmt, ast.stmt):
            stmt = parents.get(stmt)
        return stmt is not None and PRAGMA in lines[stmt.lineno - 1]

    def flag(node, message):
        if not waived(node):
            findings.append((node.lineno, message))

    def scoped_by_query(node, prefix):
        anc = parents.get(node)
        while anc is not None:
            if isinstance(anc, ast.Call) and any(
                k == f"{prefix}__user"
                or k.startswith(f"{prefix}__user_")
                or k.startswith(f"{prefix}__user__")
                for k in _spine_kwargs(anc)
            ):
                return True
            anc = parents.get(anc)
        return False

    for node in ast.walk(tree):
        # 1. Model.objects on a private model.
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "objects"
            and isinstance(node.value, ast.Name)
            and node.value.id in PRIVATE_MODELS
        ):
            methods, calls = _chain_up(node, parents)
            if SCOPES & set(methods):
                continue
            first = methods[0] if methods else None
            if first in {"create", "bulk_create"}:
                continue
            if first in {"get_or_create", "update_or_create"} and calls and "user" in calls[0]:
                continue
            flag(node, f"{node.value.id}.objects read without for_reader() or across_readers()")

        # 2. A related manager onto a private model: show.ratings.count().
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in PRIVATE_RELATIONS
            and isinstance(parents.get(node), ast.Attribute)
            and not (isinstance(node.value, ast.Attribute) and node.value.attr == "objects")
            and not _is_user(node.value)
        ):
            methods, _ = _chain_up(node, parents)
            if not SCOPES & set(methods):
                flag(node, f".{node.attr} read without for_reader() or across_readers()")

        # 3a. A keyword lookup through a private relation.
        elif isinstance(node, ast.keyword) and node.arg and "__" in node.arg:
            prefix = _private_prefix(node.arg)
            if prefix is None or node.arg.startswith(f"{prefix}__user"):
                continue
            if not scoped_by_query(node, prefix):
                flag(node, f"lookup {node.arg!r} with no {prefix}__user in the same query")

        # 3b. A string lookup passed positionally: Count("show_tags"), Q builders.
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and LOOKUP.match(node.value)
            and isinstance(parents.get(node), ast.Call)
            and node in parents[node].args
        ):
            prefix = _private_prefix(node.value)
            if prefix is not None and not scoped_by_query(node, prefix):
                flag(node, f"lookup {node.value!r} with no {prefix}__user in the same query")

    return sorted(findings)


def app_sources(root):
    """The app code the check covers: shows/ and tvlens/, less tests and migrations."""
    root = Path(root)
    for path in sorted([*root.glob("shows/**/*.py"), *root.glob("tvlens/**/*.py")]):
        if "migrations" in path.parts or path.name == "tests.py" or path.name == "reader_scope.py":
            continue
        yield path


def check_tree(root):
    """[(path, line, message)] across the app."""
    return [
        (path, line, message)
        for path in app_sources(root)
        for line, message in check_source(path.read_text(), str(path))
    ]
