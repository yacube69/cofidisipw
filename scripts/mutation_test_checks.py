"""How many typical extraction errors the accounting checks catch (CIPW-10): a mutation test.

Every value of every complete, verified ground truth file is corrupted in turn, the way OCR and the parser go
wrong (a misread digit, a dropped digit, a lost minus, a missing value, the current and prior year swapped), and the
checks run on the corrupted statement. Prints the share caught per error type and the fields no check covers.

    python scripts/mutation_test_checks.py            # errors only
    python scripts/mutation_test_checks.py --warnings # errors and plausibility warnings
    python scripts/mutation_test_checks.py --corpus   # the collected statements where every check passes, as read
                                                      # by the parser (outputs/ocr): lines the ground truth lacks
"""

import collections
import copy
import glob
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.schema import GroundTruth  # noqa: E402
from src.settings import get_settings  # noqa: E402
from src.validation.checks import validate  # noqa: E402

WITH_WARNINGS = "--warnings" in sys.argv
random.seed(7)
PERIODS = ["gross", "adjustment", "current", "prior"]


def errors(fs):
    report = validate(fs)
    found = {(c.name, c.period) for c in report.errors}
    if WITH_WARNINGS:
        found |= {(c.name, c.period) for c in report.warnings if not c.name.startswith("Value needs review")}
    return found


def mutations(v):
    """(kind, new value) for one printed value (in printed units)."""
    out = []
    s = str(abs(int(v)))
    if len(s) >= 1 and v != 0:
        i = random.randrange(len(s))
        d = str((int(s[i]) + random.choice([1, 2, 5])) % 10)
        if i == 0 and d == "0" and len(s) > 1:
            d = "1"
        out.append(("digit misread", (1 if v >= 0 else -1) * int(s[:i] + d + s[i + 1 :])))
    if v != 0:
        out.append(("lost minus / extra minus", -v))
        out.append(("digit dropped (/10)", int(v / 10)))
    out.append(("missing value", None))
    return out


stats = collections.defaultdict(lambda: [0, 0])  # kind -> [caught, total]
by_field = collections.defaultdict(lambda: [0, 0])
false_alarms = []


def _corpus() -> None:
    """Every value of every collected statement whose checks all pass, corrupted in turn (current and prior)."""
    from scripts.audit_helpers import load_pages
    from src.extraction.parser import parse_statements

    used = 0
    for _, statements in sorted(load_pages(set()).items()):
        pws = sorted((pw for p in statements.values() for pw in p), key=lambda pw: pw.page)
        fs = parse_statements(pws, {k: [pw.page for pw in v] for k, v in statements.items()})
        if errors(fs) or not validate(fs).checks:
            continue
        used += 1
        unit = fs.metadata.unit_multiplier or 1
        for statement in (fs.balance_sheet, fs.income_statement):
            for key, item in statement.items():
                for period in ("current", "prior"):
                    v = getattr(item, period)
                    if v is None or v.value is None:
                        continue
                    original = v.value
                    for kind, new in mutations(round(original / unit)):
                        v.value = None if new is None else new * unit
                        caught = bool(errors(fs))
                        v.value = original
                        stats[kind][0] += caught
                        stats[kind][1] += 1
                        by_field[key][0] += caught
                        by_field[key][1] += 1
    print(f"collected statements where every check passes: {used}")


for path in [] if "--corpus" in sys.argv else sorted(glob.glob(str(get_settings().paths.ground_truth / "[0-9]*.json"))):
    gt = GroundTruth.model_validate_json(open(path, encoding="utf-8").read())
    if gt.partial:
        continue
    base = errors(gt.to_statements())
    if base:
        false_alarms.append((path, base))
        continue
    for statement in ("balance_sheet", "income_statement"):
        for key, line in getattr(gt, statement).items():
            for period in PERIODS:
                v = getattr(line, period)
                if v is None:
                    continue
                for kind, new in mutations(v):
                    m = copy.deepcopy(gt)
                    setattr(getattr(m, statement)[key], period, new)
                    caught = bool(errors(m.to_statements()))
                    stats[kind][0] += caught
                    stats[kind][1] += 1
                    by_field[key][0] += caught
                    by_field[key][1] += 1
            # swapped columns current <-> prior
            if line.current is not None and line.prior is not None and line.current != line.prior:
                m = copy.deepcopy(gt)
                ln = getattr(m, statement)[key]
                ln.current, ln.prior = ln.prior, ln.current
                caught = bool(errors(m.to_statements()))
                stats["current/prior swapped"][0] += caught
                stats["current/prior swapped"][1] += 1

if "--corpus" in sys.argv:
    _corpus()
else:
    print("false alarms on correct ground truth:", false_alarms or "none")
print("\nby error type")
for kind, (c, t) in sorted(stats.items()):
    print(f"  {kind:28s} {c:4d}/{t:4d}  {100 * c / t:5.1f} %")
tc = sum(c for c, _ in stats.values())
tt = sum(t for _, t in stats.values())
print(f"  {'all':28s} {tc:4d}/{tt:4d}  {100 * tc / tt:5.1f} %")
print("\nfields never caught (no identity covers them):")
print("  " + ", ".join(sorted(k for k, (c, t) in by_field.items() if c == 0)))
print("\nfields caught sometimes:")
for k, (c, t) in sorted(by_field.items(), key=lambda x: x[1][0] / x[1][1]):
    if 0 < c < t:
        print(f"  {k:32s} {c}/{t}")
