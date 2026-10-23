"""Cross-name family resolution (initials, Russian base equality, typos in given names)."""
from pipeline.names.families import _damerau1, group_families
from pipeline.names.parse import parse_name


def _resolve(names):
    ps = [parse_name(*n) if isinstance(n, tuple) else parse_name(n) for n in names]
    m = group_families(ps)
    return {p.raw: m[p.family_key] for p in ps}


def test_initial_merges_into_unique_full_name():
    r = _resolve([
        "ზვიად გამსახურდიას სანაპირო",
        "პრეზიდენტ ზ. გამსახურდიას სანაპირო",
        "კონსტანტინე გამსახურდიას ქუჩა",
    ])
    assert r["პრეზიდენტ ზ. გამსახურდიას სანაპირო"] == r["ზვიად გამსახურდიას სანაპირო"]
    assert r["კონსტანტინე გამსახურდიას ქუჩა"] != r["ზვიად გამსახურდიას სანაპირო"]


def test_initial_ambiguous_is_not_merged():
    r = _resolve(["ზვიად გამსახურდიას ქუჩა", "ზაზა გამსახურდიას ქუჩა", "ზ. გამსახურდიას ქუჩა"])
    assert len({r["ზვიად გამსახურდიას ქუჩა"], r["ზაზა გამსახურდიას ქუჩა"], r["ზ. გამსახურდიას ქუჩა"]}) == 3


def test_typo_in_given_name_of_orphan_lane():
    r = _resolve(["გიორგი სააკაძის ქუჩა", "გიოგი სააკაძის I შეს."])
    assert r["გიოგი სააკაძის I შეს."] == r["გიორგი სააკაძის ქუჩა"]


def test_typo_merge_needs_primary_street():
    # both are streets: two different people, never merged by the typo rule
    r = _resolve(["ნინო ბერიძის ქუჩა", "ნანო ბერიძის ქუჩა"])
    assert r["ნინო ბერიძის ქუჩა"] != r["ნანო ბერიძის ქუჩა"]


def test_ru_base_merge():
    r = _resolve([("ახალი ხეივნის ქუჩა", "улица Ахали Хеивани"), ("ახალი ხეივანი", "Ахали Хеивани")])
    assert r["ახალი ხეივნის ქუჩა"] == r["ახალი ხეივანი"]


def test_pseudo_keys_untouched():
    r = _resolve(["თბილისი — თიანეთი", "steps of Gaara"])
    assert all(v.startswith("#") for v in r.values())


def test_damerau1():
    assert _damerau1("გიოგ", "გიორგ")
    assert _damerau1("abcd", "abdc")
    assert _damerau1("abcd", "abxd")
    assert not _damerau1("abcd", "axyd")
    assert not _damerau1("ab", "abcd")
