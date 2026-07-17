from aegis.commands.fuzzy import fuzzy_match, fuzzy_rank


def test_subsequence_matches():
    assert fuzzy_match("sp", "spawn") is not None
    assert fuzzy_match("swn", "spawn") is not None       # scattered subsequence


def test_non_subsequence_is_none():
    assert fuzzy_match("xyz", "spawn") is None


def test_empty_query_matches_with_zero_score():
    score, positions = fuzzy_match("", "spawn")
    assert positions == ()


def test_case_insensitive():
    assert fuzzy_match("SP", "spawn") is not None


def test_contiguous_outranks_scattered():
    s_contig, _ = fuzzy_match("sp", "spawn")     # "sp" adjacent
    s_scatter, _ = fuzzy_match("sn", "spawn")    # s..n scattered
    assert s_contig > s_scatter


def test_start_of_word_bonus():
    s_start, _ = fuzzy_match("q", "queues")      # at index 0
    s_mid, _ = fuzzy_match("u", "queues")        # not word-start
    assert s_start > s_mid


def test_positions_point_at_matched_chars():
    _, positions = fuzzy_match("pn", "spawn")
    assert positions == (1, 4)


def test_rank_orders_by_score_and_drops_nonmatches():
    ranked = fuzzy_rank("se", ["sessions", "spawn", "schedules"])
    assert ranked[0] == "sessions"               # best subsequence
    assert "spawn" not in ranked                 # no "se" subsequence


def test_rank_with_key():
    items = [{"n": "spawn"}, {"n": "sessions"}]
    ranked = fuzzy_rank("se", items, key=lambda d: d["n"])
    assert ranked == [{"n": "sessions"}]
