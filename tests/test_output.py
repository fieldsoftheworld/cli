from ftw_cli import output


def test_trend_draws_a_sparkline_with_the_change(capsys):
    output.configure(False)
    output.trend("fields", [100, 50, 150], [2017, 2018, 2019])
    line = capsys.readouterr().out
    assert "▅▁█" in line and "+50% since 2017" in line


def test_trend_is_silent_in_json_mode(capsys):
    output.configure(True)
    output.trend("fields", [1, 2], [2017, 2018])
    assert capsys.readouterr().out == ""


def test_cells_keep_coordinates_on_one_line():
    assert output._cell("bbox", [-93.7, 41.95]) == "-93.7, 41.95"
    assert output._cell("scenes", ["a", "b"]) == "a\nb"
