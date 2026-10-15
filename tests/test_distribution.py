"""Release archives must contain complete tests and no local artifacts."""
import importlib.util
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
    import shutil
    for archive in distributions.iterdir():
        shutil.copyfile(archive, tmp_path / archive.name)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("CLAUDE.md", "local guidance")
    with pytest.raises(ValueError, match="unexpected=.*CLAUDE"):
        policy.check_packages(tmp_path)


def test_sdist_rejects_missing_fixture(distributions, tmp_path):
    import shutil
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
