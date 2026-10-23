"""Encyclopedia build step: biographies, glossary notes, cross-references; CLI smoke test."""
from pipeline.history import enc2008
from pipeline.history.__main__ import main
from pipeline.history.enc_split import Entry


def E(section, kind, title, body, bold=None, prefix="", qualifier="", parent=None, page=30):
    return Entry(section=section, kind=kind, title=title, bold=bold or title, prefix=prefix, qualifier=qualifier,
                 page=page, body=body, parent=parent)


def test_build_resolves_eponyms_and_xrefs():
    entries = [
        E("street", "article", "ზაალ ბერიძის ქუჩა",
          "გლდანი-ნაძალადევის რ-ნი (ავჭალა); ხევიდან სხვიტორის ქუჩამდე. ადრე ვ. ლენინის სახელი ერქვა, "
          "1995 ზ. ბერიძის სახელი ეწოდა.", bold="ბერიძის ქუჩა", prefix="ზაალ"),
        E("street", "article", "ასპინძის შესახვევი",
          "დიდუბე-ჩუღურეთის რ-ნი (სვანეთისუბანი); ზეგანის ქუჩიდან ხევამდე."),
        E("street", "gloss", "ასპინძა", "დაბა სამცხეში, ასპინძის რ-ნის ცენტრი.", parent=1),
        E("renamed", "xref", "ვლადიმერ ლენინის ქუჩა", "იხ. ზაალ ბერიძის ქუჩა.", page=230),
        E("renamed", "xref", "ორთქლმავლის ქუჩა", "იხ. ასპინძის შესახვევი.", qualifier="(დიდუბე)", page=236),
        E("renamed", "xref", "მეორე ქუჩა", "იხ. სხვა ქუჩა.", page=236),
        E("bio", "bio", "ბერიძე", "ზაალ გიორგის ძე (1920–80), მხატვარი. იყო პროფესორი.", page=260),
        E("bio", "bio", "ლენინი", "ვლადიმერ ილიას ძე (1870–1924), რევოლუციონერი.", page=300),
    ]
    recs = enc2008.build(entries)
    by = {(r["entry"], r["title"]): r for r in recs}
    z = by[("article", "ზაალ ბერიძის ქუჩა")]["facts"]
    assert z["named_after"] == {"name_ka": "ზაალ ბერიძე", "birth": 1920, "death": 1980,
                                "occupation_ka": "მხატვარი", "src": "enc2008_bio"}
    lenin = [f for f in z["former"] if "ლენინის" in f["name_ka"]]
    assert len(lenin) == 1                                  # xref merged into the parsed name
    assert lenin[0]["name_ka"] == "ვლადიმერ ლენინის ქუჩა"     # initials expanded (xref / biography)
    assert lenin[0]["xref"] is True and lenin[0]["eponym"]["birth"] == 1870
    a = by[("article", "ასპინძის შესახვევი")]["facts"]
    assert a["named_after"]["name_ka"] == "ასპინძა" and a["named_after"]["src"] == "enc2008_gloss"
    assert a["former"] == [{"name_ka": "ორთქლმავლის ქუჩა", "confidence": 0.85, "xref": True, "place": "დიდუბე"}]
    orphan = by[("xref_target", "სხვა ქუჩა")]["facts"]
    assert orphan["xref_only"] is True and orphan["former"][0]["name_ka"] == "მეორე ქუჩა"
    bio = by[("bio", "ბერიძე")]
    assert bio["source"] == "enc2008_bio" and bio["facts"]["given"] == "ზაალ"


def test_cli_link_and_merge_on_empty_db(tmp_path):
    db = tmp_path / "x.sqlite"
    assert main(["link", "--db", str(db)]) == 0
    assert main(["merge", "--db", str(db)]) == 0
