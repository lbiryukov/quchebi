-- Quchebi data model v1. SQLite, WGS84. Applied by pipeline/db.py in file-name order.
-- Ownership (who writes): osm stages -> area, family, feature, feature_name, feature_area,
-- crossing, near_pair, house; enrich stages -> eponym, eponym_category, family.eponym_id,
-- llm_result, review_decision; history stages -> history_article, name_history.
-- Feature/area ids are rebuilt on every OSM build; anything that must survive a rebuild
-- (LLM cache, manual review) is keyed by family.key / qid / text, never by numeric id.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS area (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,              -- district | neighbourhood | quarter | suburb
    ka        TEXT, ru TEXT, en TEXT,
    parent_id INTEGER REFERENCES area(id),
    osm_type  TEXT, osm_id INTEGER,
    area_km2  REAL,
    lat       REAL, lon REAL,             -- label point
    geom_wkt  TEXT                        -- (MULTI)POLYGON, NULL for point-only places
);

CREATE TABLE IF NOT EXISTS eponym (
    id             INTEGER PRIMARY KEY,
    qid            TEXT UNIQUE,           -- Wikidata QID when known
    key            TEXT UNIQUE,           -- stable key when no QID (family key or normalized label)
    kind           TEXT NOT NULL,         -- person|persons_multi|group|fictional_character|toponym|event_date|nature|concept|object|no_eponym|unknown
    ka             TEXT, ru TEXT, en TEXT,
    gender         TEXT,                  -- m | f | NULL
    birth_year     INTEGER, death_year INTEGER,
    origin         TEXT,                  -- georgian | foreign | unknown
    short_ru       TEXT,                  -- one-line description in Russian (own words)
    occupations_ru TEXT,                  -- JSON array of raw occupation labels (3rd taxonomy level)
    confidence     REAL,
    source         TEXT NOT NULL,         -- osm | wd_p138 | wd_match | rules | llm | manual
    needs_review   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eponym_category (
    eponym_id INTEGER NOT NULL REFERENCES eponym(id) ON DELETE CASCADE,
    category  TEXT NOT NULL,              -- taxonomy id, e.g. lit.poet
    rank      INTEGER NOT NULL DEFAULT 0, -- 0 = main
    source    TEXT NOT NULL,
    PRIMARY KEY (eponym_id, category)
);

CREATE TABLE IF NOT EXISTS family (
    id            INTEGER PRIMARY KEY,
    key           TEXT UNIQUE NOT NULL,   -- order/spelling-insensitive eponym key
    base_ka       TEXT,                   -- eponym part as written (genitive)
    base_ru       TEXT, base_en TEXT,
    eponym_nom_ka TEXT,                   -- nominative guess
    kind_hint     TEXT,                   -- rules guess before enrichment
    eponym_id     INTEGER REFERENCES eponym(id)
);

CREATE TABLE IF NOT EXISTS feature (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,          -- canonical kind code, see docs/pack-format.md
    ka            TEXT NOT NULL,
    ru            TEXT, en TEXT,
    ru_generated  INTEGER NOT NULL DEFAULT 0,
    ord           INTEGER,                -- ordinal of a lane: "II შესახვევი" -> 2
    regnal        TEXT,                   -- regnal number inside the eponym: "ერეკლე II" -> "II"
    family_id     INTEGER REFERENCES family(id),
    parent_id     INTEGER REFERENCES feature(id),
    homonym_group INTEGER,                -- same name, separate component
    len_m         REAL,
    lat           REAL, lon REAL,         -- representative point
    bbox          TEXT,                   -- "minlat,minlon,maxlat,maxlon"
    geom_wkt      TEXT,                   -- MULTILINESTRING | POINT | (MULTI)POLYGON
    highway       TEXT,                   -- dominant OSM highway value
    area_id       INTEGER REFERENCES area(id), -- for kind district/neighbourhood
    osm_ids       TEXT,                   -- JSON array: ["w123", "n45", "r6"]
    wikidata      TEXT                    -- raw name:etymology:wikidata from OSM
);
CREATE INDEX IF NOT EXISTS feature_ka ON feature(ka);
CREATE INDEX IF NOT EXISTS feature_family ON feature(family_id);

CREATE TABLE IF NOT EXISTS feature_name (
    feature_id INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    lang       TEXT NOT NULL,             -- ka | ru | en | lat
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,             -- alt | old | official | addr_variant
    source     TEXT NOT NULL,
    PRIMARY KEY (feature_id, lang, name, kind)
);

CREATE TABLE IF NOT EXISTS feature_area (
    feature_id INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    area_id    INTEGER NOT NULL REFERENCES area(id) ON DELETE CASCADE,
    share      REAL,                      -- share of length (lines) inside the area
    PRIMARY KEY (feature_id, area_id)
);

CREATE TABLE IF NOT EXISTS crossing (
    a     INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,  -- a < b
    b     INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    lat   REAL NOT NULL, lon REAL NOT NULL,
    kind  TEXT NOT NULL,                  -- node | geom
    dir_a INTEGER NOT NULL DEFAULT 0,     -- 8-sector bitmask: directions in which a leaves the point
    dir_b INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (a, b, lat, lon)
);

CREATE TABLE IF NOT EXISTS near_pair (
    a      INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    b      INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    dist_m REAL NOT NULL,
    PRIMARY KEY (a, b)
);

CREATE TABLE IF NOT EXISTS house (
    id          INTEGER PRIMARY KEY,
    feature_id  INTEGER REFERENCES feature(id) ON DELETE SET NULL,
    area_id     INTEGER REFERENCES area(id),
    raw         TEXT NOT NULL,
    n           INTEGER,                  -- leading integer
    lit         TEXT,                     -- letter, normalized to Georgian (ა ბ გ ...)
    sub         TEXT,                     -- fraction part "3/16" -> "16"
    n_to        INTEGER,                  -- range end "3-5" -> 5
    corp        TEXT,                     -- building/korpus
    kind        TEXT,                     -- int | letter | fraction | corp | range | list | junk
    lat         REAL, lon REAL,
    addr_street TEXT,
    match       TEXT,                     -- exact | variant | normalized | no_type | fuzzy | homonym_swap | place | none
    flags       TEXT,
    osm_type    TEXT, osm_id INTEGER
);
CREATE INDEX IF NOT EXISTS house_feature ON house(feature_id);

CREATE TABLE IF NOT EXISTS history_article (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,            -- enc2008 | kawiki | ruwiki
    title       TEXT NOT NULL,
    page        TEXT,                     -- PDF page or wiki page id
    feature_id  INTEGER REFERENCES feature(id) ON DELETE SET NULL,
    family_id   INTEGER REFERENCES family(id) ON DELETE SET NULL,
    text_hash   TEXT,
    parsed_json TEXT                      -- structured facts only, never full text
);

CREATE TABLE IF NOT EXISTS name_history (
    id         INTEGER PRIMARY KEY,
    feature_id INTEGER REFERENCES feature(id) ON DELETE CASCADE,
    family_id  INTEGER REFERENCES family(id) ON DELETE SET NULL,
    name_ka    TEXT, name_ru TEXT, name_lat TEXT,
    from_year  INTEGER, to_year INTEGER,
    eponym_id  INTEGER REFERENCES eponym(id),
    source     TEXT NOT NULL,             -- enc2008 | kawiki | ruwiki | osm_old_name | addr | wikidata
    ref        TEXT,
    confidence REAL
);

CREATE TABLE IF NOT EXISTS provenance (
    entity     TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT,
    source     TEXT NOT NULL,
    ref        TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_result (
    cache_key  TEXT PRIMARY KEY,          -- sha1(prompt_version | model | dossier)
    role       TEXT NOT NULL,             -- classifier | disambiguator | history | verifier | name_qa
    model      TEXT NOT NULL,
    family_key TEXT,
    batch      TEXT,
    payload    TEXT NOT NULL,             -- validated JSON
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_decision (
    entity     TEXT NOT NULL,             -- family | eponym | name_history | feature
    entity_key TEXT NOT NULL,             -- family.key / qid / text key
    decision   TEXT NOT NULL,             -- accept | fix | reject
    payload    TEXT,
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity, entity_key)
);
