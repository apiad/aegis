from aegis.journal.fuzzy import find, match


def test_a_word_matches_its_letters_in_order_in_any_case():
    assert find("scra", "scratch-repo") == [0, 1, 2, 3]
    assert find("Rustic", "rustic-rivest") == [0, 1, 2, 3, 4, 5]
    assert find("rr", "rustic-rivest") == [0, 7]
    assert find("cmt", "commit") == [0, 2, 5]
    assert find("tsc", "scratch") is None  # out of order
    assert find("scrap", "scratch") is None


def test_the_tightest_span_is_the_one_marked():
    # "ab" first matches a…b over 0..4, but the later "ab" is tighter.
    assert find("ab", "a---bab") == [5, 6]


def test_a_word_of_three_or_more_letters_may_not_spread_too_far():
    scattered = "a" + "x" * 20 + "b" + "x" * 20 + "c"
    assert find("abc", scattered) is None
    assert find("abc", "a" + "x" * 9 + "bc") == [0, 10, 11]  # span 12 = 4 x 3
    assert find("abc", "a" + "x" * 10 + "bc") is None  # span 13
    # Two letters may spread anywhere.
    assert find("ac", scattered) == [0, len(scattered) - 1]


def test_every_word_must_match_some_field():
    fields = ["calm-hopper", "repos/notes.md", "commit", ""]
    assert match(["cmt", "notes"], "1a2b3c4 fix the parser · main", fields) == []
    assert match(["cmt", "nope"], "1a2b3c4 fix the parser · main", fields) is None
    assert match([], "anything", []) == []


def test_marks_are_the_text_indices_of_words_that_matched_the_text():
    text = "fix the parser"
    assert match(["fix", "commit"], text, ["commit"]) == [0, 1, 2]
    assert match(["PARS", "fix"], text, []) == [0, 1, 2, 8, 9, 10, 11]


def test_a_letter_that_lowers_to_two_keeps_the_fields_indices():
    assert len("İstanbul".lower()) == 9
    assert find("stan", "İstanbul") == [1, 2, 3, 4]
    assert find("STAN", "x İstanbul") == [3, 4, 5, 6]
