"""Build the Roster Tool desktop app for THIS platform, then verify it.

    python scripts/build_app.py            # production build
    python scripts/build_app.py --debug    # troubleshooting build: console window + live log

Output:
    macOS    dist/Roster Tool.app        + dist/Roster-Tool-<version>-macOS-<arch>.zip
    Windows  dist/Roster Tool/Roster Tool.exe + dist/Roster-Tool-<version>-Windows-<arch>.zip

PyInstaller cannot cross-compile: build the macOS app on a Mac and the Windows
app on Windows (see PACKAGING.md / WINDOWS_BUILD.md).

The build is rejected unless verification passes: required runtime files and
license notices present; no private rosters, test fixtures, roster-like data
files, virtualenv, git metadata or development tools inside the package; on
macOS, Info.plist's minimum system version matches the bundled binaries.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from roster_tool import APP_NAME, __version__  # noqa: E402

SPEC = ROOT / "packaging" / "roster_tool.spec"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"

# Things that must never be inside a package (directory names anywhere in a path).
FORBIDDEN_DIRS = {"private_rosters", "tests", "fixtures", ".venv", "venv", ".git", ".pytest_cache",
                  "pytest", "_pytest", "scripts", "chrome-profile"}
# Roster-like data files (the app needs none at runtime).
FORBIDDEN_SUFFIXES = {".pdf", ".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".numbers"}
# Files that must be present (matched as path suffixes anywhere in the package).
REQUIRED = [
    "roster_tool/static/index.html", "roster_tool/static/app.js", "roster_tool/static/styles.css",
    "roster_tool/assets/fonts/DejaVuSansCondensed.ttf", "roster_tool/assets/fonts/DejaVuSansCondensed-Bold.ttf",
    "roster_tool/assets/fonts/DejaVu-LICENSE.txt", "THIRD_PARTY_NOTICES.md",
    "pypdfium2-5.15.2.dist-info/licenses/LICENSES/BSD-3-Clause.txt",
]
NATIVE_PDF_ENGINE = {"darwin": "libpdfium.dylib", "win32": "pdfium.dll", "linux": "libpdfium.so"}


# --- macOS minimum-version bookkeeping ----------------------------------------------

def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def macho_minos(path: Path) -> tuple[int, int, int] | None:
    """Minimum macOS version a Mach-O binary was built for (None if unknown)."""
    from macholib.MachO import MachO
    from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
    try:
        binary = MachO(str(path))
    except Exception:
        return None
    best = None
    for header in binary.headers:
        for load_cmd, cmd, _ in header.commands:
            if load_cmd.cmd == LC_BUILD_VERSION:
                value = cmd.minos
            elif load_cmd.cmd == LC_VERSION_MIN_MACOSX:
                value = cmd.version
            else:
                continue
            version = (value >> 16, (value >> 8) & 0xFF, value & 0xFF)
            best = version if best is None or version > best else best
    return best


def required_macos(paths) -> tuple[tuple[int, int, int], Path | None]:
    best, source = (11, 0, 0), None
    for path in paths:
        if path.is_file() and not path.is_symlink() and _is_macho(path):
            minos = macho_minos(path)
            if minos and minos > best:
                best, source = minos, path
    return best, source


def runtime_binaries_before_build():
    """Native files PyInstaller will bundle: libpython, stdlib extensions, dependencies' extensions."""
    libdir = Path(sysconfig.get_config_var("LIBDIR") or "")
    yield from libdir.glob("libpython*.dylib")
    yield from Path(sysconfig.get_paths()["platstdlib"], "lib-dynload").glob("*.so")
    site = Path(sysconfig.get_paths()["purelib"])
    for package in ("pypdfium2_raw", "PIL", "reportlab"):
        if (site / package).is_dir():
            yield from (p for p in (site / package).rglob("*") if p.suffix in (".so", ".dylib"))


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(v) for v in (version if version[2] else version[:2]))


# --- Verification ---------------------------------------------------------------------

def _hashes(folder: Path) -> dict[str, str]:
    if not folder.is_dir():
        return {}
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p.name for p in folder.rglob("*") if p.is_file()}


def verify_bundle(bundle: Path, repo: Path = ROOT) -> list[str]:
    """Problems with a built package (empty list = OK). Never prints file contents."""
    problems = []
    files = [p for p in bundle.rglob("*") if p.is_file()]
    rel = [p.relative_to(bundle) for p in files]
    posix = [r.as_posix() for r in rel]

    for required in REQUIRED:
        if not any(p.endswith(required) for p in posix):
            problems.append(f"missing required file: {required}")
    engine = NATIVE_PDF_ENGINE.get(sys.platform)
    if engine and not any(Path(p).name == engine for p in posix):
        problems.append(f"missing native PDF engine: {engine}")

    for r in rel:
        bad_dirs = FORBIDDEN_DIRS & {part.lower() for part in r.parts[:-1]}
        if bad_dirs:
            problems.append(f"forbidden folder {sorted(bad_dirs)[0]!r} in package: {r.as_posix()}")
        if r.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"roster-like data file in package: {r.as_posix()}")

    sensitive = {**_hashes(repo / "tests" / "fixtures"), **_hashes(repo / "private_rosters")}
    for path, r in zip(files, rel):
        if hashlib.sha256(path.read_bytes()).hexdigest() in sensitive:
            problems.append(f"package file is a copy of a test fixture or private roster: {r.as_posix()}")
    return problems


def package_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def windows_build_in_the_way(dist: Path, name: str) -> Path | None:
    """The Windows one-folder build in dist/<name>/, if present.

    On macOS, PyInstaller uses dist/<name>/ for its intermediate output, which is the very
    folder the Windows build lives in, and it is replaced and then deleted. A project folder
    shared between both platforms would silently lose its Windows build, which can only be
    rebuilt on Windows, so the macOS build stops instead.
    """
    folder = dist / name
    return folder if (folder / f"{name}.exe").is_file() else None


# --- Build -------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--debug", action="store_true", help="console window + live log (troubleshooting build)")
    args = parser.parse_args()
    if sys.platform not in ("darwin", "win32"):
        print("Packaging is supported on macOS and Windows only.")
        return 2
    name = APP_NAME + (" Debug" if args.debug else "")
    arch = platform.machine().lower().replace("amd64", "x64")
    print(f"Building {name} {__version__} for {platform.system()} {arch} with Python {platform.python_version()}")

    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("WARNING: the git working tree has uncommitted changes; release builds should come from a commit.")

    if sys.platform == "darwin" and (windows_build := windows_build_in_the_way(DIST, name)):
        print(f"BUILD STOPPED: {windows_build.relative_to(ROOT)} holds the Windows build, and the macOS build "
              "would delete it (PyInstaller reuses that folder). Move it out of dist/ first, then build again.")
        return 1

    env = dict(os.environ, ROSTER_TOOL_DEBUG_BUILD="1" if args.debug else "0")
    if sys.platform == "darwin":
        minos, source = required_macos(runtime_binaries_before_build())
        env["ROSTER_TOOL_MACOS_MIN"] = fmt(minos)
        print(f"Bundled binaries require macOS {fmt(minos)} or later"
              + (f" (set by {source.name})" if source else ""))

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(DIST), "--workpath", str(WORK), str(SPEC)], cwd=ROOT, env=env, check=True)

    if sys.platform == "darwin":
        package = DIST / f"{name}.app"
        leftover = DIST / name  # PyInstaller's intermediate folder; the .app is the deliverable
        if leftover.is_dir() and package.is_dir():
            shutil.rmtree(leftover)
    else:
        package = DIST / name

    problems = verify_bundle(package)
    if sys.platform == "darwin":
        info = plistlib.loads((package / "Contents" / "Info.plist").read_bytes())
        actual, source = required_macos(p for p in package.rglob("*"))
        declared = tuple(int(x) for x in info["LSMinimumSystemVersion"].split("."))
        if actual[: len(declared)] > declared:
            problems.append(f"Info.plist claims macOS {info['LSMinimumSystemVersion']} but "
                            f"{source.name if source else 'a binary'} needs {fmt(actual)}")
    if problems:
        print("\nBUILD REJECTED:")
        for problem in problems:
            print("  -", problem)
        return 1

    label = "macOS" if sys.platform == "darwin" else "Windows"
    archive = DIST / f"Roster-Tool-{__version__}-{label}-{arch}{'-debug' if args.debug else ''}.zip"
    archive.unlink(missing_ok=True)
    if sys.platform == "darwin":  # ditto keeps the bundle's symlinks and permissions intact
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(package), str(archive)], check=True)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=DIST, base_dir=package.name)

    print("\nBuild verified:")
    print(f"  package: {package.relative_to(ROOT)}  ({package_size(package) / 1e6:.0f} MB, "
          f"{sum(1 for p in package.rglob('*') if p.is_file())} files)")
    print(f"  archive: {archive.relative_to(ROOT)}  ({archive.stat().st_size / 1e6:.0f} MB)")
    if sys.platform == "darwin":
        print(f"  minimum macOS: {info['LSMinimumSystemVersion']}   architecture: {arch}")
        sig = subprocess.run(["codesign", "-dv", str(package)], capture_output=True, text=True).stderr
        kind = "ad-hoc (not signed with a Developer ID; not notarized)" if "Signature=adhoc" in sig else sig.strip()
        print(f"  signature: {kind}")
    print("  contents check: no private rosters, fixtures, data files or development tools")
    print("\nNext: python scripts/smoke_test_package.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
