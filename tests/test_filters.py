import duckdb

from ftw_cli.filters import NO_FILTERS, Filters


def test_no_filters_keep_everything():
    assert NO_FILTERS.sql() == "true"


def test_filters_select_rows_by_area_and_score():
    con = duckdb.connect()
    con.execute(
        "create table f as select * from (values (100.0, 50), (5000.0, 90), (90000.0, 70))"
        ' t("metrics:area", score)'
    )

    def kept(filters: Filters) -> list[float]:
        query = f'select "metrics:area" from f where {filters.sql()} order by 1'
        return [a for (a,) in con.execute(query).fetchall()]

    assert kept(Filters(min_area=1000)) == [5000.0, 90000.0]
    assert kept(Filters(max_area=5000)) == [100.0, 5000.0]
    assert kept(Filters(min_score=80)) == [5000.0]
    assert kept(Filters(1000, 10000, 60)) == [5000.0]


def test_area_column_can_be_renamed():
    assert Filters(min_area=10).sql(area="area") == "area >= 10.0"
