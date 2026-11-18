"""The shared tag grammar, cache and matching-work budget."""

from __future__ import annotations

import pytest

from runevidence.tag_filter import (
    MATCH_BUDGET,
    InvalidTagPattern,
    TagFilter,
    TagPatternTooExpensive,
)


def spent(patterns, *tags, budget=None):
    kwargs = {} if budget is None else {"budget": budget}
    tag_filter = TagFilter(patterns, **kwargs)
    for tag in tags:
        tag_filter.matches(tag)
    return tag_filter.spent


def test_grammar_is_full_string_and_case_sensitive():
    assert TagFilter(["loss"]).matches("loss")
    assert not TagFilter(["loss"]).matches("train/loss")
    assert not TagFilter(["loss"]).matches("Loss")
    assert TagFilter(["train/*"]).matches("train/loss")
    assert TagFilter(["*loss"]).matches("train/loss")


def test_single_star_and_question_mark():
    # Empty tags are outside the 1..4,096 UTF-8 byte range and are skipped.
    assert not TagFilter(["*"]).matches("")
    assert TagFilter(["*"]).matches("a/b")
    assert TagFilter(["a?c"]).matches("abc")
    assert not TagFilter(["a?c"]).matches("ac")
    assert TagFilter(["?"]).matches("é")
    assert TagFilter(["*"]).matches("line\nbreak")


def test_escaped_metacharacters_are_literal():
    assert TagFilter([r"a\?b"]).matches("a?b")
    assert not TagFilter([r"a\?b"]).matches("axb")
    assert TagFilter([r"a\*b"]).matches("a*b")
    assert not TagFilter([r"a\*b"]).matches("axb")
    assert TagFilter([r"a\\*b"]).matches("a\\b")
    assert TagFilter([r"a\\*b"]).matches("a\\xyzb")
    assert not TagFilter([r"a\\*b"]).matches("ab")


def test_brackets_are_literal_not_a_class():
    assert TagFilter(["[ab]"]).matches("[ab]")
    assert not TagFilter(["[ab]"]).matches("a")


def test_anchoring_vectors():
    assert TagFilter(["*ab"]).matches("abab")
    assert not TagFilter(["a*a"]).matches("a")
    assert not TagFilter(["ab*"]).matches("xab")
    assert TagFilter(["*a*b"]).matches("aab")
    assert not TagFilter(["*ab*b"]).matches("ab")


def test_middle_window_and_charge_vectors():
    # middle "ab", later "b": window 4 - 1 - 0 = 3, two positions, 2 each.
    assert spent(["*ab*b"], "xxxx") == 4
    # window 2 - 1 - 0 = 1, no position: the pair is still charged once so
    # an all-wildcard pattern cannot consume unlimited matching work for free.
    assert spent(["*ab*b"], "ab") == 1
    assert spent(["*loss"], "xloss") == 4
    assert spent(["x*"], "xloss") == 1


def test_wildcard_segment_charges_the_same_as_find():
    assert spent(["x?z*"], "xyz") == 3
    assert TagFilter(["x?z*"]).matches("xyz")


def test_cache_prevents_double_charging_and_repeated_text_is_one_pair():
    tag_filter = TagFilter(["*ab*b"])
    assert not tag_filter.matches("xxxx")
    assert tag_filter.spent == 4
    assert not tag_filter.matches("xxxx")
    assert tag_filter.spent == 4

    duplicate = TagFilter(["ab", "ab"])
    assert duplicate.matches("ab")
    # The pair is keyed by pattern text, so repeating it charges once; both
    # command-line positions still count as matched for the warning pass.
    assert duplicate.spent == 2
    assert duplicate.unmatched_patterns() == ()


def test_or_selection_does_not_short_circuit_later_patterns():
    tag_filter = TagFilter(["*", "ab"])
    assert tag_filter.matches("ab")
    assert tag_filter.spent == 3


def test_unmatched_patterns_preserve_input_order():
    tag_filter = TagFilter(["*", "zzz", "a?"])
    tag_filter.matches("ab")
    assert tag_filter.unmatched_patterns() == ("zzz",)


def test_empty_and_trailing_backslash_are_invalid():
    with pytest.raises(InvalidTagPattern):
        TagFilter([""])
    with pytest.raises(InvalidTagPattern):
        TagFilter(["abc\\"])


def test_pattern_cap_counts_code_points_after_escape_processing():
    TagFilter(["a" * 1024])
    TagFilter([("a" * 1023) + r"\*"])
    TagFilter([("a" * 1023) + r"\\"])
    with pytest.raises(InvalidTagPattern):
        TagFilter(["a" * 1025])
    with pytest.raises(InvalidTagPattern):
        TagFilter([("a" * 1024) + r"\*"])


def test_lone_surrogate_pattern_never_matches_and_is_charged():
    tag_filter = TagFilter(["\udcff"])
    assert not tag_filter.matches("x")
    assert tag_filter.spent == 1


def test_lone_surrogate_tag_is_skipped_without_charge():
    tag_filter = TagFilter(["*"])
    assert not tag_filter.matches("\udcff")
    assert tag_filter.spent == 0


def test_empty_and_over_long_tags_are_skipped_without_charge():
    tag_filter = TagFilter(["*"])
    assert not tag_filter.matches("")
    assert not tag_filter.matches("a" * 4097)
    assert tag_filter.spent == 0
    assert tag_filter.matches("a" * 4096)


def test_budget_is_inclusive_at_the_exact_limit():
    tag_filter = TagFilter(["ab"], budget=2)
    assert tag_filter.matches("ab")
    assert tag_filter.spent == 2
    assert tag_filter.remaining == 0


def test_budget_raises_and_stops_further_pairs():
    tag_filter = TagFilter(["ab"], budget=1)
    with pytest.raises(TagPatternTooExpensive):
        tag_filter.matches("ab")
    with pytest.raises(TagPatternTooExpensive):
        tag_filter.matches("cd")


EXPENSIVE = "*" + ("a" * 1020) + "?b*"
EXPENSIVE_C = "*" + ("a" * 1020) + "?c*"
TAGS = tuple(("a" * 4095) + ch for ch in "cdefghijklmnopqr")


def test_expense_vector_is_3_142_650_per_pair():
    one = TagFilter([EXPENSIVE], budget=MATCH_BUDGET)
    one.matches(TAGS[0])
    assert one.spent == 3_142_650


def test_fifteen_pairs_pass_and_sixteen_refuse():
    fifteen = TagFilter([EXPENSIVE])
    for tag in TAGS[:15]:
        assert not fifteen.matches(tag)
    assert fifteen.spent == 47_139_750

    sixteen = TagFilter([EXPENSIVE])
    with pytest.raises(TagPatternTooExpensive):
        for tag in TAGS + (("a" * 4095) + "s",):
            sixteen.matches(tag)


def test_two_patterns_bound_the_budget_at_seven_tags():
    seven = TagFilter([EXPENSIVE, EXPENSIVE_C])
    for tag in TAGS[:7]:
        seven.matches(tag)
    assert seven.spent == 43_997_100

    eight = TagFilter([EXPENSIVE, EXPENSIVE_C])
    with pytest.raises(TagPatternTooExpensive):
        for tag in TAGS[:8]:
            eight.matches(tag)


def test_warning_pass_reads_the_cache():
    # The expensive pattern matches none of these tags; "*" selects all of
    # them at the one-unit minimum per pair, so a second pass charges nothing.
    tag_filter = TagFilter([EXPENSIVE, "*"])
    for tag in TAGS[:15]:
        assert tag_filter.matches(tag)
    assert tag_filter.spent == 47_139_765
    for tag in TAGS[:15]:
        assert tag_filter.matches(tag)
    assert tag_filter.spent == 47_139_765
    assert tag_filter.unmatched_patterns() == (EXPENSIVE,)



def test_repeated_expensive_pattern_text_stays_within_budget():
    repeated = TagFilter([EXPENSIVE, EXPENSIVE])
    for tag in TAGS[:15]:
        assert not repeated.matches(tag)
    assert repeated.spent == 47_139_750
    assert repeated.unmatched_patterns() == (EXPENSIVE, EXPENSIVE)


def test_adjacent_stars_are_one_wildcard():
    assert TagFilter(["a**b"]).matches("aXYZb")
    assert spent(["a**b"], "aXYZb") > 0


def test_all_wildcard_patterns_cannot_bypass_the_budget():
    patterns = ["*" * index for index in range(1, 65)]
    tag_filter = TagFilter(patterns, budget=100)
    with pytest.raises(TagPatternTooExpensive):
        for index in range(10):
            tag_filter.matches(f"tag-{index}")
