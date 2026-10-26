"""Fail on rashid's data findings (PTL-DAT) about the sample files, read from its JSON report.

The sample catalog is a bare wrapper, so rashid's metadata findings about it are expected;
PTL-DAT-017 (columns not documented in the collection) is one of those.
"""

import json
import sys
from pathlib import Path

IGNORED = {"PTL-DAT-017"}

findings = [
    f
    for f in json.loads(Path(sys.argv[1]).read_text())["findings"]
    if f["rule_id"].startswith("PTL-DAT") and f["rule_id"] not in IGNORED
]
for f in findings:
    print(f"{f['rule_id']} {f['severity']}: {f['message']}")
sys.exit(1 if findings else 0)
