import duckdb


def pytest_configure(config):
    # Install DuckDB's extensions once, before xdist starts its workers: workers that
    # all download them at once race to rename the same file, which fails on Windows.
    if not hasattr(config, "workerinput"):
        duckdb.execute("INSTALL httpfs; INSTALL spatial")
