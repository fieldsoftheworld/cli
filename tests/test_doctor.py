from ftw_cli import doctor


def test_checks_report_ok_missing_and_failed(monkeypatch):
    def missing() -> str:
        raise doctor.Missing("pip install 'ftw-cli[inference]'")

    def broken() -> str:
        raise KeyError("a bug, not a missing extra")

    monkeypatch.setattr(
        doctor, "CHECKS", {"fine": lambda: "1.0", "extra": missing, "bug": broken}
    )
    rows = {r["check"]: r for r in doctor.run()}
    assert rows["fine"] == {"check": "fine", "status": "ok", "detail": "1.0"}
    assert rows["extra"]["status"] == "missing"
    assert rows["bug"]["status"] == "failed"
