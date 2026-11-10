-- Reference notes (multi-city build): a short text with facts shown in the feature card and
-- searched by the DSL key "инфо:" (bus routes of a stop, the inscription of a plaque, year and
-- architect of a building). Written by the landmarks stage (from OSM tags) and by the reference
-- stage of Russian-language cities (pipeline/ru/). Exported as core.f[].note.
ALTER TABLE feature ADD COLUMN note TEXT;
-- Where the note came from: osm | wikidata | wikivoyage | ruwiki | llm (comma separated).
ALTER TABLE feature ADD COLUMN note_source TEXT;
