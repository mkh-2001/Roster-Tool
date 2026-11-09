"""Local performance benchmark. Prints timings only (never student data).

Usage:
    .venv/bin/python scripts/benchmark.py                      # fake fixtures (PDF, CSV, XLSX) + 300-student stress rosters
    .venv/bin/python scripts/benchmark.py path/to/roster.pdf path/to/export.xlsx --runs 50

Every file goes through the same entry point as the app (pipeline.process_roster).
Target (spec): upload -> usable roster <= 2000 ms. CSV and help sheet should be negligible.
If a number misses the target, profile before changing anything:
    .venv/bin/python -m cProfile -s cumtime scripts/benchmark.py some.pdf --runs 5 | head -40
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

TARGET_MS = 2000.0


def bench(label: str, data: bytes, fmt: str, runs: int) -> bool:
    from roster_tool.event_csv_generator import generate_csv
    from roster_tool.help_sheet_generator import generate_help_sheet
    from roster_tool.pipeline import process_roster

    t = time.perf_counter()
    first = process_roster(data, fmt)  # first run in this process (includes library warm-up)
    first_ms = (time.perf_counter() - t) * 1000
    errors = sum(1 for i in first.issues if i.severity == "error")

    # A roster with validation errors is (correctly) refused by the generators,
    # so only the reading/parsing stages can be timed for it.
    stages = list(first.timings_ms)
    output_stages = [] if errors else ["csv", "help_sheet_3day"]
    samples: dict[str, list[float]] = {k: [] for k in stages + output_stages}
    for _ in range(runs):
        result = process_roster(data, fmt)
        for k in stages:
            samples[k].append(result.timings_ms[k])
        if errors:
            continue
        t = time.perf_counter()
        generate_csv(result.roster)
        samples["csv"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        generate_help_sheet(result.roster, "Benchmark Tutor", ["Mon", "Wed", "Fri"])
        samples["help_sheet_3day"].append((time.perf_counter() - t) * 1000)

    roster = first.roster
    pages = f", {first.page_count} page(s)" if first.page_count is not None else ""
    print(f"\n{label} [{first.source_format}]: {len(data) / 1024:.0f} KB{pages}, "
          f"{len(roster.current_students)} current + {len(roster.waitlist_students)} wait-list students, "
          f"{errors} error(s)")
    if errors:
        codes = sorted({i.code for i in first.issues if i.severity == "error"})
        print(f"  VALIDATION FAILED ({', '.join(codes)}): CSV/help-sheet timing skipped")
    print(f"  first run in process: {first_ms:8.2f} ms")
    print(f"  {'stage':<18}{'median':>10}{'p95':>10}{'max':>10}   (ms, {runs} runs)")
    for k, values in samples.items():
        values = sorted(values)
        p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
        print(f"  {k:<18}{statistics.median(values):>10.2f}{p95:>10.2f}{values[-1]:>10.2f}")
    worst = max(max(samples["total"]), first_ms)
    ok = worst <= TARGET_MS
    print(f"  upload -> roster worst case {worst:.1f} ms: {'PASS' if ok else 'FAIL'} (target {TARGET_MS:.0f} ms)")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()

    t = time.perf_counter()
    import roster_tool.event_csv_generator  # noqa: F401
    import roster_tool.help_sheet_generator as hs
    import roster_tool.pipeline  # noqa: F401
    hs.ensure_fonts()
    print(f"application import + font load (once per launch): {(time.perf_counter() - t) * 1000:.1f} ms")

    ok = True
    if args.files:
        for path in args.files:
            ok &= bench(path.name, path.read_bytes(), path.suffix, args.runs)
    else:
        from fixture_builder import (ALL_FIXTURES, CSV_FIXTURE, FIXTURE_DIR, SECTIONED, XLSX_FIXTURE, FixtureSpec,
                                     SectionedSpec, build_pdf, build_sectioned_csv, build_sectioned_xlsx,
                                     make_people, rows_from)
        for spec in ALL_FIXTURES + [SECTIONED]:
            ok &= bench(spec.filename, (FIXTURE_DIR / spec.filename).read_bytes(), "pdf", args.runs)
        for name, fmt in ((CSV_FIXTURE, "csv"), (XLSX_FIXTURE, "xlsx")):
            ok &= bench(name, (FIXTURE_DIR / name).read_bytes(), fmt, args.runs)
        people = rows_from(make_people(300, seed=5))
        big = FixtureSpec("synthetic_300.pdf", "BIG 101 Stress Test", "99999", people, rows_per_page=40, stated_total=300)
        ok &= bench("synthetic 300 students / 8 pages (in memory)", build_pdf(big), "pdf", args.runs)
        big_sections = SectionedSpec("synthetic", "BIG 101 Stress Test", ["MoWe 8:00 am - 9:15 am Room 1 LEC 99999"],
                                     people, [], [], [], expected_section="99999")
        ok &= bench("synthetic 300 students (in memory)", build_sectioned_csv(big_sections), "csv", args.runs)
        ok &= bench("synthetic 300 students (in memory)", build_sectioned_xlsx(big_sections), "xlsx", args.runs)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
