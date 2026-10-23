"""Project paths and shared constants for the Quchebi data pipeline."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
RAW = DATA / "raw"                 # downloaded sources, never edited by hand
INTERIM = DATA / "interim"         # intermediate artefacts
ENRICH = DATA / "enrich"           # LLM batches, results, review decisions
BUILD = DATA / "build"             # coverage report, diffs
DB_PATH = DATA / "runcity.sqlite"

LOGS = ROOT / "logs"
MIGRATIONS = ROOT / "migrations"
APP_DATA = ROOT / "app" / "public" / "data"     # pack served by the PWA
APP_TILES = ROOT / "app" / "public" / "tiles"   # basemap, glyphs, sprites

# Tbilisi administrative boundary lookup (Overpass) and bbox (lon_min, lat_min, lon_max, lat_max)
TBILISI_AREA_QUERY = 'area["name:en"="Tbilisi"]["boundary"="administrative"]'
TBILISI_BBOX = (44.60, 41.60, 45.10, 41.87)

GEOFABRIK_GEORGIA_PBF = "https://download.geofabrik.de/europe/georgia-latest.osm.pbf"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Identify ourselves to public APIs (Overpass returns 406 without it, Wikidata asks for it).
USER_AGENT = "Quchebi/0.1 (+https://github.com/lbiryukov/quchebi)"

PACK_FORMAT_VERSION = 1


def ensure_dirs() -> None:
    """Create all data/log directories used by the pipeline."""
    for p in (RAW, INTERIM, ENRICH, BUILD, LOGS, APP_DATA, APP_TILES):
        p.mkdir(parents=True, exist_ok=True)
