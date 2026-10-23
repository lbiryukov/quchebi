"""AcadNusx -> Unicode conversion and PDF line assembly (phantom spaces, fonts)."""
from pipeline.history import acadnusx
from pipeline.history.enc_pdf import Glyph, Line, assemble, merge_same_baseline


def test_all_33_letters():
    assert acadnusx.convert("abgdevzTiklmnopJrstufqRySCcZwWxjh") == "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"


def test_words_digits_roman_and_punctuation():
    assert acadnusx.convert("quCa") == "ქუჩა"
    assert acadnusx.convert("Tbilisis 1934 w. gegmaze") == "თბილისის 1934 წ. გეგმაზე"
    # Roman numerals (X, I, V) are not part of the table; C would be (ჩ) but does not occur in them here
    assert acadnusx.convert("XIX s. II naxevarSi") == "XIX ს. II ნახევარში"
    assert acadnusx.convert("40_50-ian") == "40–50-იან"
    assert acadnusx.convert("`mTeli tfilisi~") == "„მთელი ტფილისი“"


def test_round_trip():
    s = "ვაჟა-ფშაველას გამზირი"
    assert acadnusx.convert(acadnusx.to_acad(s)) == s


def test_font_classes():
    assert acadnusx.is_georgian_font("ZMYIBV+LitNusx")
    assert acadnusx.is_georgian_font("JJWQLB+LiterMtavrBold")
    assert acadnusx.is_georgian_font("UKJEJF+AcadNusx")
    assert acadnusx.is_georgian_font("EJNRTL+KolhetyNormal")
    assert acadnusx.is_georgian_font("PKAARP+Geo-LadoGrigolia-ASTER-Bold")
    assert not acadnusx.is_georgian_font("JJWQLB+BerlinSansFB-Reg")
    assert not acadnusx.is_georgian_font("NURDEN+TimesNewRomanPS-BoldMT")
    assert acadnusx.is_mtavruli_font("JJWQLB+LiterMtavrBold")
    assert acadnusx.is_bold_font("JJWQLB+LiterMtavrBold")
    assert not acadnusx.is_mtavruli_font("ZMYIBV+LitNusx")


def _glyphs(spec, font="X+LitNusx", size=9.0, width=4.0):
    """spec: list of (char, x0); width per glyph fixed."""
    return [Glyph(ch, x, x + (width if ch != " " else 4.6), width if ch != " " else 4.6, size, font)
            for ch, x in spec]


def test_phantom_space_is_dropped_and_real_space_kept():
    # "qu Ca": the space at 8.0 is followed by 'C' drawn at 8.0 (pen moved back) -> phantom
    # then a real space at 16.0 and 'x' at 20.6 (advanced by the space width)
    spec = [("q", 0.0), ("u", 4.0), (" ", 8.0), ("C", 8.0), ("a", 12.0), (" ", 16.0), ("x", 20.6)]
    runs = assemble(_glyphs(spec))
    assert runs == [["ქუჩა ხ", "g"]]


def test_gap_without_space_glyph_becomes_space():
    spec = [("a", 0.0), ("b", 4.0), ("g", 12.0)]   # 4.0 pt gap > 0.3 em of 9 pt
    assert assemble(_glyphs(spec)) == [["აბ გ", "g"]]


def test_runs_by_font_and_plain_fonts_not_converted():
    g = _glyphs([("q", 0.0), ("u", 4.0)], font="A+LiterMtavrBold") + \
        _glyphs([(",", 8.0)], font="B+LitNusx") + _glyphs([("2", 12.6), ("5", 16.6)], font="C+BerlinSansFB-Reg")
    runs = assemble(g)
    assert runs[0] == ["ქუ", "gBM"]
    assert runs[1][1] == "g"
    assert runs[-1] == ["25", ""]


def test_soft_hyphen_ignored():
    spec = [("n", 0.0), ("\xad", 4.0), ("a", 4.0)]
    assert assemble(_glyphs(spec)) == [["ნა", "g"]]


def test_merge_same_baseline_orders_and_joins():
    a = Line(page=1, col=0, x=47, y=421.3, size=9.0, runs=[["ტანძიის ქუჩა", "gBM"], [",", "g"]])
    b = Line(page=1, col=0, x=150, y=421.5, size=9.0, runs=[["ვაკე-საბურთალოს რ-ნი", "g"]])
    c = Line(page=1, col=0, x=47, y=432.3, size=9.0, runs=[["V შეს.).", "g"]])
    out = merge_same_baseline([b, a, c])
    assert [ln.text for ln in out] == ["V შეს.).", "ტანძიის ქუჩა, ვაკე-საბურთალოს რ-ნი"]
