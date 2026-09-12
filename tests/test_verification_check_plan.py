"""Required verification is configuration-aware, not a fixed three checks."""

import pytest

from context_agent.project_checks import resolve_check_plan


@pytest.mark.parametrize("configured", [False, True])
def test_default_plan_includes_compileall_and_configured_mypy(tmp_path, configured):
    config = "[project]\nname='fixture'\n"
    if configured:
        config += "[tool.mypy]\nstrict=true\n"
    (tmp_path / "pyproject.toml").write_text(config)
    expected = ["ruff_check", "ruff_format_check", "pytest"]
    if configured:
        expected.append("mypy")
    expected.append("compileall")
    assert resolve_check_plan(tmp_path) == tuple(expected)


def test_explicit_partial_plan_is_preserved(tmp_path):
    assert resolve_check_plan(tmp_path, "pytest") == ("pytest",)


def test_invalid_manifest_does_not_silently_skip_mypy(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy\n")
    with pytest.raises(ValueError):
        resolve_check_plan(tmp_path)


def test_setup_cfg_mypy_is_required(tmp_path):
    (tmp_path / "setup.cfg").write_text("[mypy]\nstrict = true\n")
    assert "mypy" in resolve_check_plan(tmp_path)
