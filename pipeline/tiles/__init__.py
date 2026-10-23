"""Offline basemap for the PWA: Protomaps PMTiles extract of Tbilisi, glyphs and sprites.

Run ``uv run python -m pipeline.tiles`` (see ``pipeline.tiles.cli``). Outputs go to
``app/public/tiles/`` (``tbilisi.pmtiles``, ``fonts/``, ``sprites/``, ``tiles-manifest.json``);
downloaded byte ranges and assets are cached under ``data/raw/protomaps/``.
"""
