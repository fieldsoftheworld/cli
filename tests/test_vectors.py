import duckdb
import pytest

from ftw_cli import vectors


def test_utm_zone_in_both_hemispheres():
    assert vectors.utm_zone(-93.6, 42.0) == "EPSG:32615"  # Iowa
    assert vectors.utm_zone(30.3, -2.0) == "EPSG:32736"  # Rwanda


def test_crs_normalizes_codes_and_rejects_unknown_ones():
    assert vectors.crs("3857", utm="EPSG:32615") == "EPSG:3857"
    assert vectors.crs("UTM", utm="EPSG:32615") == "EPSG:32615"
    with pytest.raises(duckdb.Error):
        vectors.crs("999999", utm="EPSG:32615")
