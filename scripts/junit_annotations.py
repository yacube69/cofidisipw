"""Print the failing tests of a JUnit report as GitHub Actions annotations.

Annotations appear in the run summary and the pull request checks without opening the log, so every team member
sees which test failed and why.

Usage (CI, after pytest --junitxml): python scripts/junit_annotations.py reports/junit.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(path: str) -> int:
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        for problem in [*case.findall("failure"), *case.findall("error")]:
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            message = " ".join((problem.get("message") or problem.text or "").split())[:500]
            print(f"::error title={name}::{message}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
