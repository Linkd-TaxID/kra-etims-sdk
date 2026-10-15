#!/usr/bin/env python3
"""Check package and Pages inventories before publication."""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import zipfile

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
SCANNER_SPEC = importlib.util.spec_from_file_location(
    "repository_content_policy", ROOT / "scripts/check_repository.py")
SCANNER = importlib.util.module_from_spec(SCANNER_SPEC)
SCANNER_SPEC.loader.exec_module(SCANNER)
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
EGG_FILES = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
             "requires.txt", "top_level.txt"}
WHEEL_FILES = {"METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"}
DOC_FILES = {
    "CNAME", "index.html", "faq.html", "errors.js", "errors.json", "llms.txt",
    "robots.txt", "sitemap.xml", "assets/fuse.min.js", "assets/nav.js",
    "assets/search.js", "assets/taxid-docs.css", "assets/fonts/inter-var.woff2",
    "assets/fonts/jetbrains-mono-var.woff2", "assets/licenses/Inter-OFL.txt",
    "assets/licenses/JetBrainsMono-OFL.txt", "assets/licenses/Fuse-LICENSE.txt",
    "assets/licenses/NOTICE.txt",
}


def compare(actual: set[str], expected: set[str], label: str) -> None:
    missing, extra = expected - actual, actual - expected
    if missing or extra:
        raise ValueError(f"{label}: missing={sorted(missing)}, unexpected={sorted(extra)}")


def source_files(directory: str, suffixes: set[str]) -> set[str]:
    return {p.relative_to(ROOT).as_posix() for p in (ROOT / directory).rglob("*")
            if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts}


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or any(ord(c) < 32 for c in name):
        raise ValueError(f"Unsafe archive member: {name}")


class BoundedArchiveReader:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_ARCHIVE_BYTES

    def read(self, size=-1):
        size = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        data = self.stream.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("Archive exceeds the decompression budget")
        return data


def check_member_size(size: int, total: int, count: int, limit: int) -> int:
    if size < 0 or size > limit:
        raise ValueError("Archive member exceeds the content scan limit")
    if total + size > MAX_ARCHIVE_BYTES or count > MAX_ARCHIVE_MEMBERS:
        raise ValueError("Archive exceeds the content scan budget")
    return total + size


def check_member_content(stream, name: str, size: int, policy: dict) -> None:
    data = stream.read(size + 1)
    if len(data) != size:
        raise ValueError(f"Archive member size mismatch: {name}")
    violations = SCANNER.content_violations(data, policy)
    if violations:
        raise ValueError(f"Archive content rejected: {name}: {', '.join(violations)}")


def check_packages(directory: Path) -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    policy = json.loads((ROOT / ".github/repository-policy.json").read_text())
    limit = min(policy["max_blob_bytes"], MAX_ARCHIVE_BYTES)
    base = f"taxid_etims-{version}"
    compare({p.name for p in directory.iterdir()},
            {f"{base}.tar.gz", f"{base}-py3-none-any.whl"}, "distribution files")
    for file in directory.iterdir():
        if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError(f"Invalid or oversized distribution file: {file.name}")
    package = source_files("src/kra_etims", {".py", ".typed"})
    expected_sdist = package | source_files("tests", {".py", ".json"}) | {
        "README.md", "CHANGELOG.md", "pyproject.toml", "MANIFEST.in", "init_device.py",
        "scripts/check_distribution.py", "scripts/check_repository.py",
        ".github/repository-policy.json", "examples/pos_basket_demo.py", "PKG-INFO", "setup.cfg",
    } | {f"src/taxid_etims.egg-info/{name}" for name in EGG_FILES}
    names, actual, total = set(), set(), 0
    # Streaming checks each header before traversing its declared payload.
    with gzip.open(directory / f"{base}.tar.gz", "rb") as compressed, tarfile.open(
            fileobj=BoundedArchiveReader(compressed), mode="r|") as archive:
        for member in archive:
            safe_name(member.name)
            if member.name in names:
                raise ValueError("Duplicate sdist members")
            names.add(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Non-regular sdist member: {member.name}")
            if not member.name.startswith(base + "/") and member.name != base:
                raise ValueError(f"Unexpected sdist root: {member.name}")
            total = check_member_size(member.size, total, len(names), limit)
            if member.isfile():
                name = member.name[len(base) + 1:]
                if name not in expected_sdist:
                    raise ValueError(f"sdist: unexpected={name}")
                actual.add(name)
                with archive.extractfile(member) as stream:
                    check_member_content(stream, name, member.size, policy)
    compare(actual, expected_sdist, "sdist")
    expected_wheel = {p.removeprefix("src/") for p in package} | {
        f"{base}.dist-info/{name}" for name in WHEEL_FILES}
    with zipfile.ZipFile(directory / f"{base}-py3-none-any.whl") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel members")
        compare(set(names), expected_wheel, "wheel")
        total = 0
        for count, member in enumerate(archive.infolist(), 1):
            safe_name(member.filename)
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise ValueError(f"Non-regular wheel member: {member.filename}")
            total = check_member_size(member.file_size, total, count, limit)
        for member in archive.infolist():
            with archive.open(member) as stream:
                check_member_content(stream, member.filename, member.file_size, policy)
    print(f"Verified {base}: complete sdist tests, package-only wheel, scanned archive content")


def docs_inventory() -> set[str]:
    entries = json.loads((ROOT / "docs/errors.json").read_text())
    codes = {str(entry["code"]) for entry in entries}
    if any(not code.isalnum() for code in codes):
        raise ValueError("Result codes must be alphanumeric filenames")
    return DOC_FILES | {f"{code}.html" for code in codes}


def check_docs(directory: Path, *, stage: bool = False) -> None:
    expected = docs_inventory()
    if stage:
        if directory.resolve() == (ROOT / "docs").resolve():
            raise ValueError("Stage docs into a separate output directory")
        for name in sorted(expected):
            source = ROOT / "docs" / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Missing regular docs source: {name}")
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                raise ValueError(f"Docs destination symlink: {name}")
            shutil.copyfile(source, destination)
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Docs symlink: {path}")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    compare(actual, expected, "Pages output")
    print(f"Verified Pages output: {len(actual)} public files")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path)
    parser.add_argument("--docs-dir", type=Path)
    parser.add_argument("--stage-docs", type=Path)
    args = parser.parse_args()
    if not any((args.dist_dir, args.docs_dir, args.stage_docs)):
        parser.error("select --dist-dir, --docs-dir or --stage-docs")
    try:
        if args.dist_dir:
            check_packages(args.dist_dir)
        if args.stage_docs:
            check_docs(args.stage_docs, stage=True)
        if args.docs_dir:
            check_docs(args.docs_dir)
    except (ValueError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Distribution policy failed: {exc}\n")


if __name__ == "__main__":
    main()
