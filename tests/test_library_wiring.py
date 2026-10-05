"""Library wiring: the service loads each catalogue with the real extractor and A2 post-pass."""

from pathlib import Path

from oris_matcher.domain.attributes import extract
from oris_matcher.domain.library import load_library
from oris_matcher.service import MatchService, load_catalogue
from oris_matcher.settings import NEVER_MATCH_FILE, Settings, load_never_match

ROOT = Path(__file__).resolve().parents[1]
GLOBAL_LIBRARY = ROOT / "data" / "oris_materials_global.csv"
FR_LIBRARY = ROOT / "data" / "oris_materials_fr.csv"
NEVER_MATCH = load_never_match(ROOT / "config" / NEVER_MATCH_FILE).patterns
HMA_SUBTYPE = "Asphalt Concrete (AC) - HMA"


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        config_dir=ROOT / "config",
        libraries={"global": GLOBAL_LIBRARY, "fr": FR_LIBRARY},
    )


def test_global_plain_hma_rows_get_the_implicit_zero() -> None:
    library = load_catalogue("global", _settings())
    hma_rows = [row for row in library.rows if row.material_subtype == HMA_SUBTYPE]
    assert len(hma_rows) == 3
    assert all(row.attributes.recycled_pct == 0 for row in hma_rows)
    assert {row.material_usage for row in hma_rows} == {
        "asphalt mixture for base course",
        "asphalt mixture for binder course",
        "asphalt mixture for surface course",
    }
    rap = [row for row in library.rows if row.material_subtype == f"{HMA_SUBTYPE} 30% RAP"]
    assert rap
    assert all(row.attributes.recycled_pct == 30 for row in rap)


def test_global_catalogue_equals_the_loader_with_its_catalogue_id() -> None:
    expected = load_library(GLOBAL_LIBRARY.read_bytes(), NEVER_MATCH, catalogue="global")
    assert load_catalogue("global", _settings()) == expected


def test_fr_rows_are_untouched_by_the_post_pass() -> None:
    library = load_catalogue("fr", _settings())
    plain = load_library(FR_LIBRARY.read_bytes(), NEVER_MATCH)
    assert library == plain
    for row in library.rows:
        assert row.attributes == extract(row.normalized_text)


def test_rows_carry_extracted_attributes_and_never_match_codes() -> None:
    library = load_catalogue("global", _settings())
    concrete = [row for row in library.rows if row.material_subtype == "C30/37"]
    assert concrete
    assert all(row.attributes.strength_class == (30, 37) for row in concrete)
    assert library.never_match_codes


def test_service_uses_the_wired_loader() -> None:
    service = MatchService.from_settings(_settings())
    library = service.library("global")
    hma = next(row for row in library.rows if row.material_subtype == HMA_SUBTYPE)
    assert hma.attributes.recycled_pct == 0
    assert service.library("global") is library
