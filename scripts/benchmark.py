"""Local performance benchmark. Prints timings only (never student data).

Usage:
    .venv/bin/python scripts/benchmark.py                 # fake fixtures + a synthetic 300-student roster
    .venv/bin/python scripts/benchmark.py path/to/roster.pdf --runs 50

Target (spec): PDF upload -> usable roster <= 2000 ms. CSV and help sheet should be negligible.
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
STAGES = ("pdf_open", "text_extract", "roster_parse", "normalize", "validate", "total")


def bench(label: str, data: bytes, runs: int) -> bool:
    from roster_tool.event_csv_generator import generate_csv
    from roster_tool.help_sheet_generator import generate_help_sheet
    from roster_tool.pipeline import process_pdf

    t = time.perf_counter()
    first = process_pdf(data)  # first run in this process (includes PDFium warm-up)
    first_ms = (time.perf_counter() - t) * 1000

    samples: dict[str, list[float]] = {k: [] for k in STAGES + ("csv", "help_sheet_3day")}
    for _ in range(runs):
        result = process_pdf(data)
        for k in STAGES:
            samples[k].append(result.timings_ms[k])
        t = time.perf_counter()
        generate_csv(result.roster)
        samples["csv"].append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        generate_help_sheet(result.roster, "Benchmark Tutor", ["Mon", "Wed", "Fri"])
        samples["help_sheet_3day"].append((time.perf_counter() - t) * 1000)

    n_students = len(first.roster.students)
    errors = sum(1 for i in first.issues if i.severity == "error")
    print(f"\n{label}: {len(data) / 1024:.0f} KB, {first.page_count} page(s), {n_students} students, {errors} error(s)")
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
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()

    t = time.perf_counter()
    import roster_tool.event_csv_generator  # noqa: F401
    import roster_tool.help_sheet_generator as hs
    import roster_tool.pipeline  # noqa: F401
    hs.ensure_fonts()
    print(f"application import + font load (once per launch): {(time.perf_counter() - t) * 1000:.1f} ms")

    ok = True
    if args.pdfs:
        for path in args.pdfs:
            ok &= bench(path.name, path.read_bytes(), args.runs)
    else:
        from fixture_builder import ALL_FIXTURES, FIXTURE_DIR, FixtureSpec, build_pdf, make_people, rows_from
        for spec in ALL_FIXTURES:
            ok &= bench(spec.filename, (FIXTURE_DIR / spec.filename).read_bytes(), args.runs)
        big = FixtureSpec("synthetic_300.pdf", "BIG 101 Stress Test", "99999", rows_from(make_people(300, seed=5)),
                          rows_per_page=40, stated_total=300)
        ok &= bench("synthetic 300 students / 8 pages (in memory)", build_pdf(big), args.runs)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
