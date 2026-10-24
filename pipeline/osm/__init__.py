"""OSM import: Geofabrik PBF (or Overpass cache) -> SQLite tables of the Quchebi model.

Stages (each one clears and rewrites its own tables inside one transaction):

* :mod:`pipeline.osm.fetch`        -- download the Georgia PBF, record the snapshot in ``meta``;
* :mod:`pipeline.osm.extract`      -- read the PBF (pyosmium) or the Overpass cache into an
  :class:`~pipeline.osm.model.Extract` limited to Tbilisi;
* :mod:`pipeline.osm.areas`        -- districts and neighbourhoods (``area`` + area features);
* :mod:`pipeline.osm.features`     -- streets, water, metro, squares, parks, bridges, families;
* :mod:`pipeline.osm.landmarks`    -- museums, churches, monuments, malls ... (kinds of group place*);
* :mod:`pipeline.osm.feature_area` -- length shares of features per district / neighbourhood;
* :mod:`pipeline.osm.crossings`    -- crossings with direction masks, near pairs;
* :mod:`pipeline.osm.houses`       -- house numbers matched to streets.

:func:`pipeline.osm.run.main` runs them in order (``uv run python -m pipeline.cli osm``).
"""
