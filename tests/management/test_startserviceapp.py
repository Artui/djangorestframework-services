"""Tests for the ``startserviceapp`` management command."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

EXPECTED_PACKAGES: tuple[str, ...] = (
    "models",
    "views",
    "services",
    "selectors",
    "specs",
    "validators",
    "serializers",
    "utils",
    "migrations",
    "tests",
)

EXPECTED_TOP_LEVEL_FILES: tuple[str, ...] = (
    "__init__.py",
    "apps.py",
    "admin.py",
    "urls.py",
)


def _run(name: str, target: Path) -> None:
    """Invoke the command via Django's ``call_command`` helper."""
    target.mkdir(parents=True, exist_ok=True)
    call_command("startserviceapp", name, str(target))


def test_creates_full_layout(tmp_path: Path) -> None:
    target = tmp_path / "billing"
    _run("billing", target)

    for filename in EXPECTED_TOP_LEVEL_FILES:
        assert (target / filename).is_file(), f"missing {filename}"

    for pkg in EXPECTED_PACKAGES:
        pkg_dir = target / pkg
        assert pkg_dir.is_dir(), f"missing package dir {pkg}"
        assert (pkg_dir / "__init__.py").is_file(), f"missing {pkg}/__init__.py"


def test_apps_py_renders_app_name_and_camel_case(tmp_path: Path) -> None:
    target = tmp_path / "billing"
    _run("billing", target)

    apps_py = (target / "apps.py").read_text()
    assert 'name = "billing"' in apps_py
    assert "class BillingConfig(AppConfig):" in apps_py


def test_apps_py_handles_compound_names(tmp_path: Path) -> None:
    target = tmp_path / "user_profile"
    _run("user_profile", target)

    apps_py = (target / "apps.py").read_text()
    assert "class UserProfileConfig(AppConfig):" in apps_py


def test_urls_py_renders_app_name(tmp_path: Path) -> None:
    target = tmp_path / "billing"
    _run("billing", target)

    urls_py = (target / "urls.py").read_text()
    assert 'app_name = "billing"' in urls_py


def test_template_files_renamed_from_py_tpl(tmp_path: Path) -> None:
    target = tmp_path / "billing"
    _run("billing", target)

    # No `.py-tpl` files should remain in the rendered output.
    leftover = list(target.rglob("*.py-tpl"))
    assert leftover == [], f"unrendered template files remain: {leftover}"


def test_missing_name_raises_command_error(tmp_path: Path) -> None:
    with pytest.raises(CommandError):
        call_command("startserviceapp")


def test_invalid_name_raises_command_error(tmp_path: Path) -> None:
    target = tmp_path / "out"
    target.mkdir()
    # Django rejects names that aren't valid identifiers.
    with pytest.raises(CommandError):
        call_command("startserviceapp", "1bad-name", str(target))


def test_user_supplied_template_overrides_default(tmp_path: Path) -> None:
    """``--template`` should win over the bundled service_app template."""
    custom = tmp_path / "custom_template"
    custom.mkdir()
    (custom / "__init__.py-tpl").write_text("")
    (custom / "marker.py-tpl").write_text("MARKER = True\n")

    target = tmp_path / "out"
    target.mkdir()
    call_command("startserviceapp", "out", str(target), template=str(custom))

    assert (target / "marker.py").is_file()
    # The bundled layout should NOT have been written (custom template only
    # defined `__init__` and `marker`).
    assert not (target / "services").exists()
