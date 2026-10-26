import subprocess
import sys


def ftw(*args: str) -> subprocess.CompletedProcess:
    code = (
        f"import sys; from ftw_cli.cli import run; sys.argv = {['ftw', *args]!r}; run()"
    )
    return subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )


def test_bad_input_is_one_line_without_a_traceback():
    result = ftw("data", "query", "1,2,3")
    assert result.returncode == 1
    assert "error: bbox needs 4 comma-separated numbers" in result.stderr
    assert "Traceback" not in result.stderr
