# Episode synopsis search moves to FTS5 (#29). The point is bm25 ranking
# within the episode branch; the speed is a side effect worth having.
#
# The table is a plain self-contained FTS5 table, not external-content and not
# contentless, and that is a decision:
#
# - External content must name one table or view whose columns it can read
#   back. shows_episode has no show_id; it lives a join away on shows_season.
#   Feeding it through a view means every query pays the join and every
#   trigger must hand old values back through the special 'delete' command,
#   where one missed path corrupts the index silently instead of erroring.
# - Contentless cannot return any column but rowid, which defeats the reason
#   the id columns exist: #29 carries show_id, season_id and episode_id so the
#   season pivot (#25, #26) is a SELECT change, not an index rebuild.
# - Self-contained duplicates the synopsis text. #29 measured that at +37MB on
#   a 73MB database and accepted it.
#
# The three ids are UNINDEXED: they are payload to read back, not text to
# tokenize. rowid is the episode pk, which is what lets the triggers address
# rows directly. Every episode is indexed, empty synopses included: an empty
# string contributes no tokens, and an unconditional mirror keeps the
# invariant checkable (one FTS row per episode row) with no drift between what
# the populate step kept and what the triggers maintain.
#
# Sync is triggers, live at write time, not a rebuild riding along with
# ingest (#29 decided this before building). The update trigger fires on
# season_id as well as overview because TMDb moves episodes between seasons
# while keeping their id (see Ingestor._upsert_child); an overview-only
# trigger would leave the moved episode answering for its old show. Django
# saves write every column, so the OF list costs nothing there and only
# narrows raw SQL updates.

from django.db import migrations

CREATE = """
CREATE VIRTUAL TABLE episode_fts USING fts5(
    overview,
    show_id UNINDEXED,
    season_id UNINDEXED,
    episode_id UNINDEXED
);
"""

# Measured 2026-09-01: 164,360 episodes indexed in under a second.
POPULATE = """
INSERT INTO episode_fts(rowid, overview, show_id, season_id, episode_id)
SELECT e.id, e.overview, s.show_id, e.season_id, e.id
FROM shows_episode e JOIN shows_season s ON s.id = e.season_id;
"""

# sqlite3 executes one statement per call, so each trigger is its own string
# rather than one script; a trigger body's internal semicolons make naive
# splitting impossible anyway.
TRIGGER_INSERT = """
CREATE TRIGGER episode_fts_after_insert AFTER INSERT ON shows_episode BEGIN
    INSERT INTO episode_fts(rowid, overview, show_id, season_id, episode_id)
    SELECT new.id, new.overview, s.show_id, new.season_id, new.id
    FROM shows_season s WHERE s.id = new.season_id;
END;
"""

TRIGGER_UPDATE = """
CREATE TRIGGER episode_fts_after_update
AFTER UPDATE OF overview, season_id ON shows_episode BEGIN
    DELETE FROM episode_fts WHERE rowid = old.id;
    INSERT INTO episode_fts(rowid, overview, show_id, season_id, episode_id)
    SELECT new.id, new.overview, s.show_id, new.season_id, new.id
    FROM shows_season s WHERE s.id = new.season_id;
END;
"""

TRIGGER_DELETE = """
CREATE TRIGGER episode_fts_after_delete AFTER DELETE ON shows_episode BEGIN
    DELETE FROM episode_fts WHERE rowid = old.id;
END;
"""

REVERSE = [
    "DROP TRIGGER IF EXISTS episode_fts_after_insert;",
    "DROP TRIGGER IF EXISTS episode_fts_after_update;",
    "DROP TRIGGER IF EXISTS episode_fts_after_delete;",
    "DROP TABLE IF EXISTS episode_fts;",
]


class Migration(migrations.Migration):
    dependencies = [
        ("shows", "0009_similarshow_cast_contribution_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            [CREATE, POPULATE, TRIGGER_INSERT, TRIGGER_UPDATE, TRIGGER_DELETE],
            REVERSE,
        ),
    ]
