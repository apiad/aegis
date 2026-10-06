import random
import re

from aegis.tui.names import (
    ADJECTIVES, ADJECTIVES_BY_LETTER, LAUREATES, generate_name,
)


def test_format_alliterates_and_members():
    n = generate_name(set(), random.Random(0))
    assert re.fullmatch(r"[a-z]+-[a-z]+", n)
    adj, last = n.split("-")
    # Alliteration: adjective initial matches laureate initial.
    assert adj[0] == last[0], n
    assert last in LAUREATES
    assert adj in ADJECTIVES_BY_LETTER[last[0]]


def test_seeded_deterministic():
    assert generate_name(set(), random.Random(7)) == \
           generate_name(set(), random.Random(7))


def test_all_letters_in_LAUREATES_have_adjective_pool():
    """Invariant: every laureate's first letter must have ≥5
    adjectives so cycling within a person is meaningful."""
    for last in LAUREATES:
        pool = ADJECTIVES_BY_LETTER.get(last[0])
        assert pool is not None, f"{last} has no adjective pool for letter {last[0]!r}"
        assert len(pool) >= 5, (
            f"letter {last[0]!r} pool size {len(pool)} < 5")


def test_no_laureate_reused_until_pool_exhausted():
    """First len(LAUREATES) handles must hit distinct laureates."""
    rng = random.Random(42)
    taken: set[str] = set()
    seen_lasts: list[str] = []
    for _ in range(len(LAUREATES)):
        n = generate_name(taken, rng)
        taken.add(n)
        seen_lasts.append(n.split("-")[1])
    # All distinct.
    assert len(set(seen_lasts)) == len(LAUREATES), seen_lasts


def test_no_adjective_reuse_until_pool_exhausted():
    """No adjective is reused across the session until every adjective
    has appeared at least once (or no fresh combo remains)."""
    rng = random.Random(7)
    taken: set[str] = set()
    seen_adjs: list[str] = []
    # Total adjectives across all letters.
    total_adjs = sum(len(pool)
                     for pool in ADJECTIVES_BY_LETTER.values())
    # Draw up to either total laureates or total adjectives, whichever
    # is smaller — at that point every pick can still get a fresh adj
    # AND fresh laureate.
    n_picks = min(len(LAUREATES), total_adjs)
    for _ in range(n_picks):
        n = generate_name(taken, rng)
        taken.add(n)
        seen_adjs.append(n.split("-")[0])
    # All distinct adjectives across the first n_picks.
    assert len(set(seen_adjs)) == n_picks, seen_adjs


def test_letter_cycles_when_choices_are_equal():
    """When a fresh letter is available, the picker should rotate to
    it rather than mining the same letter twice in a row."""
    rng = random.Random(0)
    taken: set[str] = set()
    seen_letters: list[str] = []
    # First few picks should give us distinct letters (we have 19
    # different initial letters in LAUREATES — easy budget).
    for _ in range(10):
        n = generate_name(taken, rng)
        taken.add(n)
        seen_letters.append(n.split("-")[1][0])
    # All distinct.
    assert len(set(seen_letters)) == 10, seen_letters


def test_no_keen_knuth_then_keen_kahn():
    """Regression for the specific case Alex saw: keen-knuth followed
    by keen-kahn should not happen — keen is already used, score
    drops, picker switches to a non-keen adjective."""
    rng = random.Random(12345)
    taken = {"keen-knuth"}
    # Now ask for many names; none should reuse "keen" until the
    # adjective pool can no longer afford avoiding it.
    saw_keen_again_when_alternatives_existed = False
    for _ in range(30):
        n = generate_name(taken, rng)
        taken.add(n)
        if n.startswith("keen-") and n != "keen-knuth":
            # Did another adjective exist that was unused?
            used_adj = {h.split("-")[0] for h in taken if h != n}
            any_unused_adj = any(
                a not in used_adj
                for pool in ADJECTIVES_BY_LETTER.values()
                for a in pool)
            if any_unused_adj:
                saw_keen_again_when_alternatives_existed = True
                break
    assert not saw_keen_again_when_alternatives_existed


def test_cycles_adjectives_within_a_laureate_when_reused():
    """If a laureate must be reused, the adjective changes."""
    rng = random.Random(0)
    # Pin the only "available" pair starting fresh on knuth.
    # Force the situation by pre-taking every other laureate with
    # at least one pair each, leaving knuth free.
    taken: set[str] = set()
    for L in LAUREATES:
        if L == "knuth":
            continue
        # Use the first available adjective for each.
        adj = ADJECTIVES_BY_LETTER[L[0]][0]
        taken.add(f"{adj}-{L}")
    # First call must pick knuth (it's the only unused laureate).
    n1 = generate_name(taken, rng)
    assert n1.endswith("-knuth"), n1
    taken.add(n1)
    # Now ALL laureates have been used. Next pick will reuse someone;
    # the adjective MUST be one not already paired with that person.
    n2 = generate_name(taken, rng)
    assert n2 not in taken
    adj2, last2 = n2.split("-")
    # Alliteration still holds.
    assert adj2[0] == last2[0]


def test_numeric_suffix_fallback_when_everything_taken():
    # Take every alliterating pair for every laureate.
    taken: set[str] = set()
    for L in LAUREATES:
        for a in ADJECTIVES_BY_LETTER[L[0]]:
            taken.add(f"{a}-{L}")
    n = generate_name(taken, random.Random(0))
    # Should end with -<digit>.
    assert re.search(r"-\d+$", n), n
    assert n not in taken


def test_backward_compat_flat_adjectives_export():
    """ADJECTIVES is still exported as a flat tuple — external callers
    that imported it before the per-letter restructuring keep working."""
    assert isinstance(ADJECTIVES, tuple)
    assert "lucid" in ADJECTIVES
