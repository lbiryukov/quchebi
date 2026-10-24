# Query language (DSL) v1 — normative spec

Implemented in `app/src/dsl/` (lexer → parser → AST → evaluator over bitsets). Tests:
`tests/js/dsl.*.test.mjs` plus golden queries in `tests/vectors/dsl_golden.json`.

## Lexical rules

- Input is one line. Case-insensitive everywhere. `ё` = `е`. Keys accept `:` or `=` as separator.
- Tokens: words (letters of any script, digits, `-`, `*`, `.`, `/` inside house numbers),
  quoted strings `"..."`, regex literals `/.../`, `(`, `)`, `|`, `,`, `~`, comparison operators
  `> < >= <= =` (only after numeric keys), unary `-` / `!` / `не` at the start of an atom.
- `или` (as a separate word) = `|`. `не` (as a separate word) = unary NOT.

## Grammar (EBNF)

```
query    = or_expr ;
or_expr  = and_expr { ("|" | "или") and_expr } ;
and_expr = unary { unary } ;                         (* juxtaposition = AND *)
unary    = ("-" | "!" | "не") unary | atom ;
atom     = "(" or_expr ")" | filter | text ;
filter   = key (":" | "=") value | numkey cmp number ;
value    = list | range | sub ;
sub      = "(" or_expr ")" [ "~" ( number | compass ) ] ;
list     = item { "," item } ;                       (* comma = OR between values *)
item     = word | quoted | regex ;
range    = number "-" number ;
cmp      = ">" | "<" | ">=" | "<=" | "=" ;
compass  = "с" | "св" | "в" | "юв" | "ю" | "юз" | "з" | "сз"
         | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw" ;
text     = word | quoted | regex ;
```

## Keys

Russian key first, then synonyms (all equivalent).

| key | synonyms | value | meaning |
|---|---|---|---|
| (none) | | text | substring of the skeleton of any name: ka, ru, en, alt, old |
| `имя` | `n`, `name` | text, `^text`, `text$`, quoted, regex | stricter name match (see Text) |
| `тип` | `t`, `type` | list of kind names; `name*` = group | kind filter |
| `район` | `р`, `d` | list of district names (prefix/substring on ka/ru/en) | official district |
| `в` | `in` | list of place or district names | inside area polygon (≥30 m or ≥50%, like the zone rule) |
| `кто` | `e`, `who` | list of taxonomy ids/aliases, kind aliases, or raw occupation words | eponym category |
| `био` | `bio` | text | substring in eponym `desc` + `occ` + names |
| `век` | `c` | `19` or `11-12` | eponym lived in that century (overlap of birth..death) |
| `пол` | `g` | `м`/`ж` (`m`/`f`) | eponym gender |
| `свой` | `ge` | `да`/`нет` | eponym is Georgian / foreign |
| `дом` | `h` | `3`, `=3`, `3а`, `1-9`, `3?`, `есть`, `нет` | house numbers (see Houses) |
| `домов` | `hn` | `cmp number` | number of known house numbers |
| `x` | `пересекает`, `cross` | sub, optionally `~compass` | crosses a feature of the subquery |
| `рядом` | `near` | sub or `я` (`me`), optionally `~metres` (default 200) | within distance |
| `семья` | `f`, `fam` | text or sub | all members of the families of matched features |
| `№` | `ord`, `no` | number or range | lane ordinal |
| `было` | `old` | text or sub (eponym filters only) | former names |
| `длина` | `len` | `cmp number` (metres) | length (inside the zone when zone is on) |
| `зона` | `zone` | `выкл`/`off`, `задевает`/`touch`, `целиком`/`whole` | zone mode for this query |
| `сорт` | `sort` | `имя`, `длина`, `домов`, `расстояние` | result order (not a filter) |

Unknown key → error with the closest known key (Damerau–Levenshtein ≤ 2) as suggestion.
Unknown value for an enumerated key (`тип`, `кто`, `район`, `в`, `пол`, `свой`, `зона`, `сорт`)
→ error listing up to 5 closest values.

## Semantics

Every atom evaluates to a bitset over features (`core.f`). AND/OR/NOT are bitwise; NOT is
relative to all features. The **zone** (if set and not disabled) is applied once, to the final
top-level result, using the zone mode (default rule: ≥30 m or ≥50% of the length inside;
points: inside; polygons: intersects). Inside relations the zone acts as described below.

### Text
- Skeleton: lowercase → transliterate Georgian and Cyrillic to Latin (`app/src/search/normalize.js`,
  mirrored by `pipeline/names/translit.py`, shared vectors `tests/vectors/translit.json`) →
  drop everything but `[a-z0-9]` → `gh→g`, `q→k`, `w→v`, `y→i`, `f→p` (Georgian ფ is written
  both «п» and «ф» in Russian). A bare word matches when its
  skeleton is a substring of the skeleton of any name variant of the feature (ka, ru, en, alt,
  old ka/ru/lat). Quoted text is one token (spaces are dropped by the skeleton anyway).
- `имя:^x` / `имя:x$`: prefix/suffix on the skeleton of the **base name** (name without the type
  word and the ordinal), or on the full name.
- Regex `/.../` is applied (case-insensitive) to the raw ka and ru names and old names. Before
  compiling, `\С` expands to a consonant class `[бвгджзйклмнпрстфхцчшщბგდვზთკლმნპჟრსტფქღყშჩცძწჭხჯჰ]` and
  `\Г` to a vowel class `[аеёиоуыэюяაეიოუ]`. Invalid regex → error.

### Kinds
`тип:переулок,тупик` = OR. `тип:улица*` = the whole group of `улица` (street*). Synonyms from
`core.kinds[].syn` are accepted.

Landmarks (group `place*`, docs/pack-format.md): `тип:музей`, `тип:храм` (= церковь, собор,
монастырь), `тип:памятник` (= статуя, бюст, мемориал), `тип:доска` (memorial plaques), `тип:тц`,
`тип:вокзал` (= автовокзал), `тип:институт` ... `тип:место*` (= `тип:места*`, `тип:place*`) is the
whole group; `тип:место` alone is the generic kind (attractions, viewpoints, artworks, historic
buildings). Landmarks have no family and no house numbers of their own: the address is a house row
of the parent street, so `дом:8 рядом:(тип:музей истори)~50` finds the street of the museum.

### Eponyms (`кто`, `био`, `век`, `пол`, `свой`)
A feature's eponym is `eponyms.e[core.fam[f.fam].e]`. `кто:` value resolution order: taxonomy id
(`lit.poet`), taxonomy alias (`поэт`, group alias `литература` = all children), kind alias
(`персона` = kind person or persons_multi, `топоним`, `событие`, `природа`, `понятие`, `группа`),
then raw occupation word: matches if any `occ` item's normalized form starts with the value
(`кто:хормейстер`). If nothing matches → error with suggestions.

### Houses (`дом`)
Rows from `houses.h[f]`. When the zone is on, only houses whose point is inside the zone polygon
count (not the zone rule for lines).
- `дом:3` — any house with `n == 3` (3, 3ა, 3/16, 3 корп. 2) or a range/list containing 3.
- `дом:=3` — exactly `n == 3` with empty `lit` and `sub`.
- `дом:3а` / `дом:3ა` / `дом:3a` — `n == 3` and `lit == "ა"` (letters normalized: а/a→ა, б/b→ბ, в/v→ვ, г/g→გ, д/d→დ, е/e→ე).
- `дом:3/16` — `n == 3`, `sub == "16"`.
- `дом:1-9` — any `n` in [1, 9].
- `дом:3?` — like `дом:3`, plus features where 3 is "probably there": the same-parity side has
  numbers below and above 3 (e.g. 1 and 5, or 1 and 7) but not 3.
- `дом:есть` / `дом:нет` — has at least one / no house numbers (zone-restricted if zone on).
- `домов>30` — count of house rows (zone-restricted if zone on).

### Relations
- `x:(Q)` — features `f` with a crossing `(f, g)` where `g ∈ eval(Q)`. The subquery is evaluated
  **without** the zone (a poet's street may extend outside), but the crossing point must be inside
  the zone when the zone is on. `x:(Q)~в` additionally requires `f`'s direction mask at that
  crossing to contain the sector "E".
- `рядом:(Q)~300` — features with any geometry point within 300 m of any geometry point of a
  feature in `eval(Q)` (Q's geometry restricted to the zone when on). `рядом:я~500` uses the
  current position `ctx.me` (`{lat, lon}`); missing → error "нет геолокации — нажмите «Где я»".
- `семья:text` = `семья:(text)`: all features of every family that contains a matched feature.
- `было:text` — skeleton substring on `old[]` names. `было:(Q)` — Q may only contain eponym filters
  (`кто`, `био`, `век`, `пол`, `свой`) and text; matches features with an old name whose eponym
  satisfies Q (text inside matches the old name itself).

### Zone mode
`зона:выкл` disables the final zone filter and all zone restrictions inside relations and houses.
`зона:задевает` — any part inside. `зона:целиком` — whole length inside (≥ 99%).

### Sorting
Default: relevance for text queries (exact base-name match first, then prefix, then substring),
otherwise by name (ka collation of the ru name if present). `сорт:расстояние` uses `ctx.me` or
the map centre.

## Output of `evaluate()`

```js
{
  bits,            // Bitset of matched features (after zone)
  count,           // popcount
  order,           // Int32Array of feature indices in display order
  explain,         // Russian sentence, e.g. "Переулки, пересекающие улицы (кто: поэт) — в зоне"
  warnings,        // e.g. ["зона выключена"]
  timeMs
}
```
Parse errors: `{error: {pos, len, message, suggestions: [...]}}` with `pos` in characters.

## Autocomplete

`complete(text, cursor, index, ctx) → [{label, insert, detail}]`:
- at a key position: keys whose names/synonyms start with the typed prefix;
- after `key:`: values for enumerated keys with counts in the current zone (`detail: "41"`);
- inside `x:(` etc.: recursive.

## Extensions and decisions made in the implementation (24/10/2026)

None of these change the meaning of queries written to the spec above.
- Equivalent spellings: `x:word` / `рядом:word` = `x:(word)`; full compass names after `~`
  (`~восток`); units `км`/`м` in `длина` and `рядом`; Roman numerals in `век`; `>`/`<` also for
  `№` and `век`; typographic quotes, `≥`, `≤`; spaces after `:` and around `,`; plural forms and an
  unambiguous 3+ letter prefix in `тип:`; pattern classes `\с \г \C \V` besides `\С \Г`; the word
  «и» is skipped with a warning.
- An unclosed bracket, quote or pattern at the end of the line is closed automatically with a
  warning; `parse(text, {strict: true})` makes it an error.
- `зона:` and `сорт:` are lifted to the top level; inside a subquery or under `-` they are an error.
  Default zone mode is the zone's own rule (`ZoneMasks.rule`); `зона:вкл` restores it.
- Subqueries of `x:`, `рядом:`, `семья:` are evaluated fully without the zone (houses included);
  the zone applies only to the crossing point / geometry.
- `имя:x` and `имя:"…"` match current names only (not old names); bare words match old names too.
- `век:`: if only one life year is known, the other is taken as ±70 years.
- Fuzzy fallback: a bare word with no exact hits matches skeleton tokens within Damerau–Levenshtein
  1 (4–5 letters) or 2 (6+ letters), `x` = `ks`; a warning lists the fuzzy pairs.
- API additions: `evaluate()` may return `error` (unknown enumerated value) with `count = 0`;
  `run(text, index, ctx)` parses and evaluates; `prepare(index, {all})` warms caches;
  `complete()` items carry `from`/`to` (range to replace).

### Ranking and places (added with the landmarks layer)

- Text relevance: whole-phrase match first (100 × 0–3), then per word: 4 whole name, 3 name word,
  2 prefix, 1 substring, 0 old name only; Georgian genitive `-s/-is` and final `-i` are tolerated;
  base names of both the family and the feature count.
- Ties: streets, lanes and squares → other features → numbered/derived lanes → main places
  (museum, theatre, church, monument…) → plaques, schools, universities, housing, factories.
  Without text and without `сорт:`, places come after everything else.
- Fuzzy streets: if a 6+ letter word matches only non-street features exactly, fuzzy street
  matches are added (warning "нечёткое совпадение среди улиц: …" when they reach the result).
- Places without a Russian name but with an English one are also indexed as "<ru kind> <en name>"
  (`тип:музей иллюзий` finds "Museum of Illusions").

## Golden examples (must hold on the real pack; counts are checked against SQL in tests)

```
дом:3
тип:улица* дом:3
тип:переулок x:(тип:улица* кто:поэт)
кто:хормейстер дом:24
кто:наука дом:2
кто:дирижёр было:крылов
было:(био:гэс)
имя:/\С{5}/
x:(тип:сквер рижск)~в дом:16
x:(чаргали)
тип:тупик дом:нет в:сололаки
кто:писатель пол:ж
тип:улица -кто:персона
рядом:(тип:метро)~300 тип:переулок*
семья:важа-пшавела
```
