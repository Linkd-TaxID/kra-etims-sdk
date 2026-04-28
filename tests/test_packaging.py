"""
Packaging verification tests.

Fast tests (always run):
  - Version string is valid semver and matches pyproject.toml
  - __version__ is exposed at the package root
  - py.typed marker file exists (PEP 561 compliance)
  - CLI entry point module is importable
  - etims --help exits 0

Slow tests (marked @pytest.mark.slow — skip with -m 'not slow'):
  - python -m build --wheel --no-isolation produces a .whl
  - The wheel contains py.typed
  - The wheel installs in a fresh venv and `etims tax bands` runs offline

Run slow tests:
    pytest tests/test_packaging.py -m slow -s
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

# tomllib is stdlib on 3.11+; on 3.10 use the backport so inline
# `import tomllib` inside test methods resolves correctly.
if sys.version_info < (3, 11):
    import tomli as _tomllib_backport
    sys.modules.setdefault("tomllib", _tomllib_backport)

import pytest

# Root of the source tree (one level above tests/)
_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Fast tests — always run
# ===========================================================================

class TestVersionConsistency:
    def test_version_is_exposed(self) -> None:
        import kra_etims
        assert hasattr(kra_etims, "__version__")
        assert isinstance(kra_etims.__version__, str)

    def test_version_is_semver(self) -> None:
        import kra_etims
        assert re.match(r"^\d+\.\d+\.\d+", kra_etims.__version__), (
            f"__version__ {kra_etims.__version__!r} is not semver"
        )

    def test_version_matches_pyproject(self) -> None:
        import tomllib
        import kra_etims

        pyproject = _ROOT / "pyproject.toml"
        with open(pyproject, "rb") as f:
            meta = tomllib.load(f)
        declared = meta["project"]["version"]
        assert kra_etims.__version__ == declared, (
            f"__version__ ({kra_etims.__version__!r}) != "
            f"pyproject.toml version ({declared!r})"
        )


class TestPEP561Compliance:
    def test_py_typed_marker_exists_in_source(self) -> None:
        marker = _ROOT / "src" / "kra_etims" / "py.typed"
        assert marker.exists(), f"py.typed missing at {marker}"

    def test_py_typed_in_package_data(self) -> None:
        import tomllib
        with open(_ROOT / "pyproject.toml", "rb") as f:
            meta = tomllib.load(f)
        pkg_data = meta.get("tool", {}).get("setuptools", {}).get("package-data", {})
        assert "py.typed" in pkg_data.get("kra_etims", []), (
            "py.typed not listed in [tool.setuptools.package-data]"
        )


class TestEntryPoint:
    def test_cli_module_importable(self) -> None:
        from kra_etims.cli.main import main, app
        assert callable(main)
        assert app is not None

    def test_etims_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "kra_etims.cli.main", "--help"],
            capture_output=True, text=True, cwd=str(_ROOT),
        )
        # Typer exits 0 on --help
        assert result.returncode == 0, result.stderr

    def test_etims_version_flag_or_help_shows_name(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "kra_etims.cli.main", "--help"],
            capture_output=True, text=True, cwd=str(_ROOT),
        )
        assert "etims" in result.stdout.lower()

    def test_offline_command_works_as_subprocess(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "kra_etims.cli.main", "tax", "bands", "--json"],
            capture_output=True, text=True, cwd=str(_ROOT),
            env={**__import__("os").environ, "TAXID_API_KEY": ""},
        )
        assert result.returncode == 0, result.stderr
        import json
        bands = json.loads(result.stdout)
        assert len(bands) == 5
        band_map = {b["band"]: b["rate"] for b in bands}
        assert band_map["B"] == "16%"   # B is Standard VAT — guard against inversion
        assert band_map["A"] == "0%"    # A is Exempt, NOT 16%


class TestDependencyDeclarations:
    def test_core_deps_declared(self) -> None:
        import tomllib
        with open(_ROOT / "pyproject.toml", "rb") as f:
            meta = tomllib.load(f)
        deps = meta["project"]["dependencies"]
        dep_names = [d.split(">=")[0].split("[")[0].lower() for d in deps]
        assert "pydantic" in dep_names
        assert "httpx" in dep_names

    def test_cli_optional_deps_declared(self) -> None:
        import tomllib
        with open(_ROOT / "pyproject.toml", "rb") as f:
            meta = tomllib.load(f)
        cli_deps = meta["project"]["optional-dependencies"]["cli"]
        dep_names = [d.split(">=")[0].lower() for d in cli_deps]
        assert "typer" in dep_names
        assert "rich" in dep_names
        assert "platformdirs" in dep_names
        assert "keyring" in dep_names

    def test_scripts_entry_point_declared(self) -> None:
        import tomllib
        with open(_ROOT / "pyproject.toml", "rb") as f:
            meta = tomllib.load(f)
        scripts = meta["project"].get("scripts", {})
        assert "etims" in scripts
        assert scripts["etims"] == "kra_etims.cli.main:main"

    def test_requires_python_is_310_or_higher(self) -> None:
        import tomllib
        with open(_ROOT / "pyproject.toml", "rb") as f:
            meta = tomllib.load(f)
        req = meta["project"]["requires-python"]
        # Must be >=3.10 (SDK uses tomllib, match-case, etc.)
        assert "3.10" in req or "3.1" in req


# ===========================================================================
# Slow tests — build wheel, install, smoke test
# ===========================================================================

@pytest.mark.slow
def test_wheel_builds(tmp_path: Path) -> None:
    """python -m build --wheel --no-isolation produces exactly one .whl."""
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, got: {wheels}"


@pytest.mark.slow
def test_wheel_contains_py_typed(tmp_path: Path) -> None:
    """The built wheel must include the py.typed PEP 561 marker."""
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(tmp_path)],
        capture_output=True, cwd=str(_ROOT), check=True,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert any("py.typed" in n for n in names), (
        f"py.typed not found in wheel. Contents: {names}"
    )


@pytest.mark.slow
def test_wheel_installs_and_cli_runs(tmp_path: Path) -> None:
    """
    Build wheel → install in fresh venv → run `etims tax bands --json`.
    Verifies the full distribution chain end-to-end.
    """
    import os, venv

    # Build wheel
    build_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(build_dir)],
        capture_output=True, cwd=str(_ROOT), check=True,
    )
    wheel = next(build_dir.glob("*.whl"))

    # Create fresh venv
    venv_dir = tmp_path / "venv"
    venv.create(str(venv_dir), with_pip=True)
    venv_python = venv_dir / "bin" / "python"

    # Install wheel + cli extras
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet",
         f"{wheel}[cli]"],
        capture_output=True, check=True,
    )

    # Run etims tax bands --json from the installed command
    etims_bin = venv_dir / "bin" / "etims"
    result = subprocess.run(
        [str(etims_bin), "tax", "bands", "--json"],
        capture_output=True, text=True,
        env={**os.environ, "TAXID_API_KEY": ""},
    )
    assert result.returncode == 0, result.stderr
    import json
    bands = json.loads(result.stdout)
    assert len(bands) == 5
    assert any(b["band"] == "B" and b["rate"] == "16%" for b in bands)
