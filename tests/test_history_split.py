"""Hyphen joining and splitting of the book into entries (synthetic lines)."""
import collections

from pipeline.history.enc_pdf import Line
from pipeline.history.enc_split import join_hyphen, join_lines, split_entries


def test_join_hyphen_uses_vocabulary():
    vocab = collections.Counter({"დიდუბე-ჩუღურეთის": 40, "რ-ნი": 300, "წარმოიქმნა": 500})
    assert join_hyphen("ძველი წარ-", "მოიქმნა XX ს.", vocab) == "ძველი წარმოიქმნა XX ს."
    assert join_hyphen("რნი დიდუბე-", "ჩუღურეთის რ-ნი", vocab) == "რნი დიდუბე-ჩუღურეთის რ-ნი"
    assert join_hyphen("რ-", "ნი (ვაკე)", vocab) == "რ-ნი (ვაკე)"
    assert join_hyphen("XX ს. 70-", "იან წლებში", vocab) == "XX ს. 70-იან წლებში"
    # unknown word: join without hyphen (line-end hyphenation is the common case)
    assert join_hyphen("ლებარ-", "დის ქუჩიდან", vocab) == "ლებარდის ქუჩიდან"


def test_join_lines_soft_hyphen_and_spaces():
    vocab = collections.Counter()
    assert join_lines(["ერთხანს ნაფა\xad", "რეულის ქუჩა", "ერქვა."], vocab) == "ერთხანს ნაფარეულის ქუჩა ერქვა."


def L(page, y, runs, size=9.0, col=0):
    return Line(page=page, col=col, x=47.0, y=y, size=size, runs=[list(r) for r in runs])


def test_split_entries_sections_and_kinds():
    lines = [
        L(16, 740, [("თბილისი. ქუჩები", "gB")], size=12.2),              # running header (skipped)
        L(16, 700, [("ასხის ქუჩა", "gBM"), (", დიდუბე-ჩუღურეთის რ-ნი (ახალი კუკია); ლებარ-", "g")]),
        L(16, 690, [("დის ქუჩიდან ცაიშის ქუჩამდე. ადრე ასხინის ქუჩა ერქვა.", "g")]),
        L(16, 680, [("ასხი", "gM"), (", კარსტული მასივი ეგრისის ქედზე.", "g")]),
        L(16, 670, [("თედორე ", "g"), ("აფანასიევის ქუჩა", "gBM"), (", ისანი-სამგორის რ-ნი", "g")]),
        L(16, 660, [("(საბარგოს დასახლება); ადრე საბარგოს დასახლების V ქუჩა ერქვა.", "g")]),
        L(16, 650, [("ნავთლუღის ", "gBM"), ("I ", "gB"), ("დასახლების:", "gBM")]),
        L(16, 640, [("I ", "gB"), ("ქუჩა", "gBM"), (", იხ. ვასილი კოპცოვის ქუჩა.", "g")]),
        L(16, 630, [("V ", "gB"), ("ქუჩა", "gBM"), (", კახეთის გზატკეცილიდან უზნაძის ქუჩამდე.", "g")]),
        L(16, 620, [("ალი ", "g"), ("ტაგი", "gBM"), ("-", "g"), ("ზადეს ქუჩა", "gBM"), (", იხ. ფირდოუსის ქუჩა.", "g")]),
        L(17, 400, [("saxelSecvlili obieqtebi", "")], size=38.8),
        L(18, 640, [("აგურის ქუჩა ", "gBM"), ("(ახალი კუკია), იხ. ზურაბ ქვლივიძის", "g")], size=10.0),
        L(18, 630, [("ქუჩა.", "g")], size=10.0),
        L(19, 400, [("biografiuli statiebi", "")], size=38.8),
        L(20, 700, [("ბებელი ", "gBM"), ("ავგუსტ (1840–1913), გერმანიისა და საერთაშ.", "g")]),
        L(20, 690, [("მუშათა მოძრაობის მოღვაწე.", "g")]),
    ]
    es = split_entries(lines)
    kinds = [(e.section, e.kind, e.title) for e in es]
    assert ("street", "article", "ასხის ქუჩა") in kinds
    assert ("street", "gloss", "ასხი") in kinds
    assert ("street", "article", "თედორე აფანასიევის ქუჩა") in kinds
    assert ("street", "group", "ნავთლუღის I დასახლების") in kinds
    assert ("street", "sub_xref", "ნავთლუღის I დასახლების I ქუჩა") in kinds
    assert ("street", "sub", "ნავთლუღის I დასახლების V ქუჩა") in kinds
    assert ("street", "xref", "ალი ტაგი-ზადეს ქუჩა") in kinds
    assert ("renamed", "xref", "აგურის ქუჩა") in kinds
    assert ("bio", "bio", "ბებელი") in kinds
    by = {e.title: e for e in es}
    assert by["ასხის ქუჩა"].body.startswith("დიდუბე-ჩუღურეთის რ-ნი (ახალი კუკია); ლებარდის ქუჩიდან")
    assert by["აგურის ქუჩა"].qualifier == "(ახალი კუკია)"
    assert by["აგურის ქუჩა"].body == "იხ. ზურაბ ქვლივიძის ქუჩა."
    assert es[[e.title for e in es].index("ასხი")].parent == [e.title for e in es].index("ასხის ქუჩა")
    assert by["ბებელი"].body.startswith("ავგუსტ (1840–1913)")
