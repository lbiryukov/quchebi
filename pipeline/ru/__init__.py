"""Reference stage of Russian-language cities (``City.lang == "ru"``, e.g. Moscow districts).

The Tbilisi stages ``pipeline.history`` and ``pipeline.enrich`` are built around Georgian sources
(the 2008 encyclopedia, Georgian grammar, the Georgian gazetteer). A Russian-language city gets the
same facts from other sources, written into the same tables:

* ``fetch``   -- Wikidata items of the district (streets, buildings, monuments), "named after"
                 statements, facts about the eponyms, Russian Wikipedia articles of the streets
                 (wikitext, cached in ``data/<city>/raw`` only) and the Wikivoyage heritage lists;
* ``batches`` -- dossiers for the LLM subagents: ``street-researcher`` (Opus: extraction of the
                 origin of the name, former names and notable buildings from the article text) and
                 ``eponym-classifier`` (Haiku: taxonomy categories of the eponyms);
* ``merge``   -- validated results -> ``eponym``, ``eponym_category``, ``family.eponym_id``,
                 ``name_history``, ``feature.note`` and the ``house`` landmarks.

Run: ``QUCHEBI_CITY=khamovniki uv run python -m pipeline.ru fetch|batches|merge|all``.
Only facts are stored (years, names, who a street is named after), never article texts.
"""
