"""Line grouping, thousands-group merging and deskew mapping."""

import pytest

from src.extraction.layout import NumberToken, group_lines, merge_number_tokens, straighten, unstraighten
from src.extraction.types import PageWords, Word
from src.schema import BBox


def w(text, x0, y0, x1=None, h=8.0, conf=None):
    return Word(text=text, x0=x0, y0=y0, x1=x1 if x1 is not None else x0 + 6 * len(text), y1=y0 + h, conf=conf)


def test_group_lines_keeps_rows_apart_and_sorts_words():
    words = [w("700", 300, 100.5), w("Zásoby", 80, 100), w("038", 250, 101), w("Materiál", 80, 112)]
    lines = group_lines(words)
    assert [line.text for line in lines] == ["Zásoby 038 700", "Materiál"]


def test_merge_joins_thousands_groups_but_not_columns():
    # "46 863 840" in one column, "-17 406 042" in the next one (gap 40 pt)
    words = [w("46", 300, 0), w("863", 315, 0), w("840", 336, 0), w("-17", 397, 0), w("406", 418, 0), w("042", 439, 0)]
    tokens = merge_number_tokens(words)
    assert [t.text for t in tokens] == ["46 863 840", "-17 406 042"]
    assert all(isinstance(t, NumberToken) for t in tokens)


def test_merge_does_not_join_a_short_group():
    tokens = merge_number_tokens([w("12", 0, 0), w("70", 15, 0)])
    assert [t.text for t in tokens] == ["12", "70"]


def test_label_words_pass_through():
    tokens = merge_number_tokens([w("Zásoby", 0, 0), w("038", 60, 0)])
    assert isinstance(tokens[0], Word) and isinstance(tokens[1], NumberToken)


def test_straighten_then_unstraighten_roundtrip():
    pw = PageWords(file="x", page=1, source="ocr", width=595, height=842, skew=1.2, words=[w("A", 500, 300)])
    straight = straighten(pw)[0]
    back = unstraighten(BBox(x0=straight.x0, y0=straight.y0, x1=straight.x1, y1=straight.y1), pw)
    assert back.x0 == pytest.approx(500, abs=0.2) and back.y0 == pytest.approx(300, abs=0.2)


def test_straighten_aligns_a_skewed_row():
    # two words of one row, 400 pt apart, on a page whose rows rise ~8 pt to the right;
    # estimate_skew reports such a page as a negative angle (see test_estimate_skew_recovers_rotation)
    pw = PageWords(
        file="x", page=1, source="ocr", width=595, height=842, skew=-1.2,
        words=[w("left", 100, 400), w("right", 500, 400 - 400 * 0.02094)],
    )
    a, b = straighten(pw)
    assert abs(a.cy - b.cy) < 1.0
    assert len(group_lines([a, b])) == 1
