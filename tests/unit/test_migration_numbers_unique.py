"""Guard: every migration file carries a distinct numeric prefix.

The runner applies `backend/migrations/*.sql` in lexical order, so two files
sharing a number still run, but their relative order is decided by the slug
after the underscore, not by anything the author chose. Four pairs already
exist (0021, 0022, 0024, 0032) and are grandfathered because renaming an
applied migration would re-apply it on every existing database. New
collisions fail here: take the next free number at merge time.
"""

from collections import Counter
from pathlib import Path

from backend.app.migrations_runner import _num_prefix

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
GRANDFATHERED = {"0021", "0022", "0024", "0032"}


def test_migration_numbers_are_unique() -> None:
    nums = Counter(n for p in MIGRATIONS.glob("*.sql") if (n := _num_prefix(p.name)))
    dupes = {n for n, c in nums.items() if c > 1} - GRANDFATHERED
    assert not dupes, f"migration numbers used twice: {sorted(dupes)}"
