"""Project paths and shared constants for the Quchebi data pipeline.

The pipeline builds one data pack per *city* (a city or a city district). The city is chosen with
the environment variable ``QUCHEBI_CITY`` (or ``python -m pipeline.cli --city <id> ...``) before any
pipeline module is imported; every path below is derived from it. Tbilisi (the default) keeps the
original paths (``data/``, ``app/public/data/``); other cities live in ``data/<id>/`` and
``app/public/data/<id>/``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class City:
    id: str
    title: str                       # shown in the client's district picker
    lang: str                        # language of the primary OSM name: "ka" | "ru"
    bbox: tuple[float, float, float, float]   # lon_min, lat_min, lon_max, lat_max
    pbf_url: str
    pbf_prefix: str                  # local file name: <prefix>-YYYYMMDD.osm.pbf
    boundary: dict[str, str]         # tags of the boundary relation (all must match)
    district_level: str              # admin_level of the districts inside the boundary
    boundary_buffer_m: float = 0.0   # keep objects up to this far outside the boundary
    extra_kinds: bool = False        # bus stops, embassies, hotels ... (pipeline/names/parse.py)
    local_origin: str = "ge"         # eponyms.e[].o code of a "local" eponym
    local_countries: tuple[str, ...] = ()     # Wikidata QIDs of the "local" states
    attribution: tuple[str, ...] = field(default_factory=tuple)


CITIES: dict[str, City] = {
    "tbilisi": City(
        id="tbilisi", title="Тбилиси", lang="ka",
        bbox=(44.60, 41.60, 45.10, 41.87),
        pbf_url="https://download.geofabrik.de/europe/georgia-latest.osm.pbf", pbf_prefix="georgia",
        boundary={"boundary": "administrative", "admin_level": "4", "name:en": "Tbilisi"},
        district_level="10",
    ),
    "khamovniki": City(
        id="khamovniki", title="Москва. Хамовники", lang="ru",
        bbox=(37.535, 55.705, 37.620, 55.755),
        # Geofabrik has no Moscow-only file (and refuses cloud addresses); OSM France has one.
        pbf_url="https://download.openstreetmap.fr/extracts/russia/central_federal_district/moscow-latest.osm.pbf",
        pbf_prefix="moscow",
        boundary={"boundary": "administrative", "admin_level": "8", "name": "район Хамовники"},
        district_level="8",
        boundary_buffer_m=80.0,      # both sides of the border streets (Garden Ring, boulevards)
        extra_kinds=True,
        local_origin="ru",
        # Russia, Russian Empire, USSR, RSFSR, Tsardom of Russia, Grand Duchy of Moscow, Russian Republic
        local_countries=("Q159", "Q34266", "Q15180", "Q2184", "Q186096", "Q170770", "Q139319"),
    ),
}

CITY_ID = os.environ.get("QUCHEBI_CITY", "tbilisi").strip().lower() or "tbilisi"
if CITY_ID not in CITIES:
    raise SystemExit(f"QUCHEBI_CITY={CITY_ID!r}: unknown city; known: {sorted(CITIES)}")
CITY = CITIES[CITY_ID]
IS_DEFAULT_CITY = CITY_ID == "tbilisi"

DATA = ROOT / "data" if IS_DEFAULT_CITY else ROOT / "data" / CITY_ID
RAW = DATA / "raw"                 # downloaded sources, never edited by hand
INTERIM = DATA / "interim"         # intermediate artefacts
ENRICH = DATA / "enrich"           # LLM batches, results, review decisions
BUILD = DATA / "build"             # coverage report, diffs
DB_PATH = DATA / "runcity.sqlite"

LOGS = ROOT / "logs"
MIGRATIONS = ROOT / "migrations"
APP_PUBLIC = ROOT / "app" / "public"
# pack served by the PWA (Tbilisi: app/public/data, other cities: app/public/data/<id>)
APP_DATA = APP_PUBLIC / "data" if IS_DEFAULT_CITY else APP_PUBLIC / "data" / CITY_ID
APP_TILES = APP_PUBLIC / "tiles"                # basemaps (<city>.pmtiles), shared glyphs and sprites
# Tile manifest of the city (Tbilisi keeps the original file name).
TILES_MANIFEST_NAME = "tiles-manifest.json" if IS_DEFAULT_CITY else f"{CITY_ID}.tiles-manifest.json"

# Tbilisi administrative boundary lookup (Overpass) and bbox (lon_min, lat_min, lon_max, lat_max)
TBILISI_AREA_QUERY = 'area["name:en"="Tbilisi"]["boundary"="administrative"]'
TBILISI_BBOX = CITIES["tbilisi"].bbox
CITY_BBOX = CITY.bbox

GEOFABRIK_GEORGIA_PBF = CITIES["tbilisi"].pbf_url
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
