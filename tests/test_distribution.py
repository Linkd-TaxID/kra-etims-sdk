"""Release archives must contain complete tests and no local artifacts."""
import importlib.util
import io
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("distribution_policy", ROOT / "scripts/check_distribution.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    configured = os.environ.get("TAXID_DIST_DIR")
    if configured:
        return Path(configured).resolve()
    directory = tmp_path_factory.mktemp("release")
    subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(directory)],
                   cwd=ROOT, check=True, capture_output=True)
    return directory


def test_archive_membership(distributions):
    policy.check_packages(distributions)


def test_unpacked_sdist_contracts(distributions, tmp_path):
    policy.check_packages(distributions)
    archive_path = next(distributions.glob("*.tar.gz"))
    with tarfile.open(archive_path) as archive:
        # Membership validation above has rejected links and traversal paths.
        archive.extractall(tmp_path)
    unpacked = next(tmp_path.iterdir())
    env = {**os.environ, "PYTHONPATH": str(unpacked / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_phase4_operations.py", "tests/test_withholding.py",
         "tests/test_item_wire_contract.py"],
        cwd=unpacked, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wheel_rejects_local_artifact(distributions, tmp_path):
    for archive in distributions.iterdir():
        shutil.copyfile(archive, tmp_path / archive.name)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("CLAUDE.md", "local guidance")
    with pytest.raises(ValueError, match="unexpected=.*CLAUDE"):
        policy.check_packages(tmp_path)


def test_sdist_rejects_missing_fixture(distributions, tmp_path):
    original = next(distributions.glob("*.tar.gz"))
    shutil.copyfile(next(distributions.glob("*.whl")), tmp_path / next(distributions.glob("*.whl")).name)
    with tarfile.open(original) as source, tarfile.open(tmp_path / original.name, "w:gz") as target:
        for member in source:
            if member.name.endswith("tests/fixtures/phase4-remittance-case.json"):
                continue
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
    with pytest.raises(ValueError, match="missing=.*phase4-remittance-case"):
        policy.check_packages(tmp_path)


def test_docs_stage_excludes_source_and_rejects_extra_file(tmp_path, monkeypatch):
    source = tmp_path / "source"
    docs = source / "docs"
    for name in policy.DOC_FILES | {"000.html"}:
        path = docs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public asset")
    (docs / "errors.json").write_text('[{"code": "000"}]')
    (docs / "CONTRIBUTING.md").write_text("source documentation")
    monkeypatch.setattr(policy, "ROOT", source)
    output = tmp_path / "site"
    policy.check_docs(output, stage=True)
    assert not (output / "CONTRIBUTING.md").exists()
    (output / "phase4-verification.md").write_text("internal notes")
    with pytest.raises(ValueError, match="unexpected=.*phase4-verification"):
        policy.check_docs(output)


def rewrite_archive(distributions, directory, kind, replacement):
    for original in distributions.iterdir():
        shutil.copyfile(original, directory / original.name)
    if kind == "wheel":
        path = next(directory.glob("*.whl"))
        with zipfile.ZipFile(path) as archive:
            members = [(entry, archive.read(entry)) for entry in archive.infolist()]
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry, data in members:
                if entry.filename.endswith(".dist-info/METADATA"):
                    data = replacement(data)
                archive.writestr(entry, data)
    else:
        path = next(directory.glob("*.tar.gz"))
        with tarfile.open(path) as archive:
            members = [(entry, archive.extractfile(entry).read() if entry.isfile() else None)
                       for entry in archive.getmembers()]
        with tarfile.open(path, "w:gz") as archive:
            for entry, data in members:
                if entry.name.endswith("/PKG-INFO"):
                    data = replacement(data)
                    entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data) if data is not None else None)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_existing_archive_member_secret_is_rejected(distributions, tmp_path, kind):
    marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    rewrite_archive(distributions, tmp_path, kind, lambda data: data + b"\n" + marker)
    with pytest.raises(ValueError, match="Archive content rejected:.*private-key"):
        policy.check_packages(tmp_path)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_archive_member_size_is_bounded(distributions, tmp_path, kind):
    limit = json_policy_limit()
    rewrite_archive(distributions, tmp_path, kind, lambda data: b"x" * (limit + 1))
    with pytest.raises(ValueError, match="Archive member exceeds the content scan limit"):
        policy.check_packages(tmp_path)


def json_policy_limit():
    import json
    return json.loads((ROOT / ".github/repository-policy.json").read_text())["max_blob_bytes"]


def test_archive_total_size_and_member_count_are_bounded():
    with pytest.raises(ValueError, match="content scan budget"):
        policy.check_member_size(1, policy.MAX_ARCHIVE_BYTES, 1, 1024)
    with pytest.raises(ValueError, match="content scan budget"):
        policy.check_member_size(1, 0, policy.MAX_ARCHIVE_MEMBERS + 1, 1024)


def test_decompression_budget_covers_hidden_tar_headers(monkeypatch):
    monkeypatch.setattr(policy, "MAX_ARCHIVE_BYTES", 16)
    reader = policy.BoundedArchiveReader(io.BytesIO(b"x" * 32))
    with pytest.raises(ValueError, match="decompression budget"):
        reader.read(1024 * 1024)
