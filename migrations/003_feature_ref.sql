-- Reference facts attached to a feature by the reference stage of a city (pipeline/ru/merge.py):
-- one short text per source. The export joins them after feature.note (the OSM-derived note), so a
-- re-run of the reference stage replaces its own rows and never touches the OSM note.
CREATE TABLE IF NOT EXISTS feature_ref (
    feature_id INTEGER NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,             -- wikivoyage | ruwiki | wikidata
    rank       INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    PRIMARY KEY (feature_id, source, rank)
);
