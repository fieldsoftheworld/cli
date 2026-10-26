import pyarrow.parquet as pq
import pytest

from ftw_cli import fiboa, ftw2e, match, vectors

X0, Y0 = 450_000, 4_650_000  # near Ames, in UTM zone 15N
# (x0, x1) of 100 m tall squares
REFERENCE = [(0, 100), (200, 300), (400, 500), (520, 620), (700, 1050)]
PREDICTION = [(0, 100), (250, 350), (400, 620), (700, 850), (850, 1000), (2000, 2100)]


def boxes(spans: list[tuple[int, int]]) -> str:
    rows = ", ".join(f"({i}, {x0}, {x1})" for i, (x0, x1) in enumerate(spans, 1))
    return f"""
        select 'f' || i as id,
               ST_SetCRS(ST_MakeEnvelope({X0} + x0, {Y0}, {X0} + x1, {Y0} + 100), 'EPSG:32615') as geometry
        from (values {rows}) t(i, x0, x1)
    """


@pytest.fixture
def con():
    con = ftw2e.connect()
    match.load(con, "a", boxes(REFERENCE))
    match.load(con, "b", boxes(PREDICTION))
    assert match.project(con, "a", "b") == "EPSG:32615"
    return con


def test_scores_count_matches_merges_and_splits(con):
    result = match.compare(con, iou=0.5)
    assert (result.reference, result.prediction, result.matched) == (5, 6, 1)
    assert result.precision == round(1 / 6, 4) and result.recall == 0.2
    assert result.mean_iou == 1.0
    assert result.matched_area == round(10_000 / 75_000, 4)
    assert (result.merges, result.splits) == (1, 1)


def test_lower_threshold_matches_greedily_one_to_one(con):
    # b2 (IoU 1/3), b3 (0.45 with a3 and a4: a3 wins the tie), b4 (0.43 with a5: b4 wins over b5)
    assert match.compare(con, iou=0.3).matched == 4
    matched = con.execute("select ar, br from matches order by ar").fetchall()
    assert matched == [(1, 1), (2, 2), (3, 3), (5, 4)]


def test_a_set_matches_itself_exactly():
    con = ftw2e.connect()
    match.load(con, "a", boxes(REFERENCE))
    match.load(con, "b", boxes(REFERENCE))
    match.project(con, "a", "b")
    result = match.compare(con)
    assert (result.precision, result.recall, result.f1, result.mean_iou) == (1, 1, 1, 1)
    assert (result.merges, result.splits) == (0, 0)


def test_files_in_different_crs_match(tmp_path):
    con = ftw2e.connect()
    geo, parquet = tmp_path / "a.geojson", tmp_path / "b.parquet"
    reproject = "select id, ST_Transform(geometry, '{}') as geometry from ({})"
    vectors.write(con, reproject.format("EPSG:4326", boxes(REFERENCE)), geo, {})
    vectors.write(
        con,
        reproject.format("EPSG:3857", boxes(REFERENCE)),
        parquet,
        fiboa.collection("t"),
        crs="EPSG:3857",
    )
    match.load_file(con, "a", geo)
    match.load_file(con, "b", parquet)
    match.project(con, "a", "b")
    result = match.compare(con)
    assert (result.matched, result.merges, result.splits) == (5, 0, 0)
    assert result.mean_iou > 0.999


def test_write_prediction_adds_the_match(con, tmp_path):
    match.compare(con, iou=0.5)
    out = tmp_path / "matches.parquet"
    assert match.write_prediction(con, out) == 6
    rows = {r["id"]: r for r in pq.read_table(out).to_pylist()}
    assert rows["f1"]["matched_id"] == "f1" and rows["f1"][
        "match_iou"
    ] == pytest.approx(1.0)
    assert rows["f6"]["matched_id"] is None and rows["f6"]["match_iou"] is None


@pytest.mark.integration
def test_changes_add_up_to_each_years_fields():
    con = ftw2e.connect()
    box = ftw2e.BBox(-93.70, 41.95, -93.60, 42.05)
    match.load(con, "a", *ftw2e.select(con, box, 2018))
    match.load(con, "b", *ftw2e.select(con, box, 2024))
    match.project(con, "a", "b")
    rows = match.changes(con)
    assert sum(r.fields_from for r in rows) == ftw2e.summarize(con, box, 2018)["fields"]
    assert sum(r.fields_to for r in rows) == ftw2e.summarize(con, box, 2024)["fields"]


def test_changes_label_every_field_of_both_years(tmp_path):
    con = ftw2e.connect()
    match.load(con, "a", boxes([*REFERENCE, (3000, 3100)]))  # the last overlaps nothing
    match.load(con, "b", boxes(PREDICTION))
    match.project(con, "a", "b")
    rows = {c.change: c for c in match.changes(con, iou=0.5)}
    counts = {name: (c.fields_from, c.fields_to) for name, c in rows.items()}
    assert counts == {
        "kept": (1, 1),
        "changed": (1, 1),
        "split": (1, 2),
        "merged": (2, 1),
        "new": (0, 1),
        "lost": (1, 0),
    }
    assert (rows["split"].area_from_ha, rows["split"].area_to_ha) == (3.5, 3.0)
    assert (rows["merged"].area_from_ha, rows["merged"].area_to_ha) == (2.0, 2.2)

    out = tmp_path / "changes.parquet"
    assert (
        match.write_changes(con, out, 2018, 2024) == 7
    )  # all of 2024, and 2018's lost
    table = pq.read_table(out).to_pylist()
    assert {(r["year"], r["change"]) for r in table if r["id"] == "f6"} == {
        (2024, "new"),
        (2018, "lost"),
    }
