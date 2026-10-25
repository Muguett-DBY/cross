"""Independent boundary checks for relative isolation roots; runner-only execution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import platformdirs
from platformdirs.testing import isolated_dirs


def test_pin_on_entry_not_manager_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(tmp_path)
    manager = isolated_dirs("sandbox")
    monkeypatch.chdir(first)
    with manager as root:
        assert root == first / "sandbox"
        monkeypatch.chdir(second)
        actual = platformdirs.PlatformDirs("app", ensure_exists=True).user_cache_path
        assert actual == first / "sandbox/user_cache/app"
        assert actual.is_dir()
        assert not (second / "sandbox").exists()


def test_nested_relative_exception_restores_outer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, third = [tmp_path / name for name in ("first", "second", "third")]
    for path in (first, second, third):
        path.mkdir()
    monkeypatch.chdir(first)
    saved_class_attributes = dict(vars(platformdirs.PlatformDirs))
    instance = platformdirs.PlatformDirs("app", version="v1", ensure_exists=True)
    with isolated_dirs("outer"):
        monkeypatch.chdir(second)
        outer_path = first / "outer/user_config/app/v1"
        assert instance.user_config_path == outer_path
        with pytest.raises(LookupError, match="probe"):
            with isolated_dirs(Path("inner")) as inner:
                monkeypatch.chdir(third)
                assert inner == second / "inner"
                assert instance.user_config_path == second / "inner/user_config/app/v1"
                assert instance.user_config_path.is_dir()
                raise LookupError("probe")
        assert instance.user_config_path == outer_path
        assert outer_path.is_dir()
        assert not (third / "inner").exists()
        assert not (second / "outer").exists()
    assert dict(vars(platformdirs.PlatformDirs)) == saved_class_attributes


def test_parent_relative_pathlike_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    with isolated_dirs(Path("..") / "pinned") as root:
        assert root.is_absolute()
        monkeypatch.chdir(second)
        actual = platformdirs.PlatformDirs("app", ensure_exists=True).user_data_path
        assert actual.resolve() == tmp_path / "pinned/user_data/app"
        assert actual.is_dir()
        assert not (second / "pinned").exists()


def test_absolute_root_spelling_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    second = tmp_path / "second"
    second.mkdir()
    absolute_root = tmp_path / "uncreated" / ".." / "pinned"
    with isolated_dirs(absolute_root) as root:
        monkeypatch.chdir(second)
        assert root == absolute_root
        assert platformdirs.user_cache_path("app") == absolute_root / "user_cache/app"
        assert not (tmp_path / "uncreated").exists()


def test_relative_symlink_spelling_and_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, target = [tmp_path / name for name in ("first", "second", "target")]
    for path in (first, second, target):
        path.mkdir()
    alias = first / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Runner cannot create directory symlinks: {error}")
    monkeypatch.chdir(first)
    with isolated_dirs("alias") as root:
        assert root == alias
        monkeypatch.chdir(second)
        actual = platformdirs.PlatformDirs("app", ensure_exists=True).user_cache_path
        assert actual == alias / "user_cache/app"
        assert actual.resolve() == target / "user_cache/app"
        assert actual.is_dir()
        assert not (second / "alias").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative path semantics")
def test_windows_drive_relative_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.chdir(tmp_path)
    assert tmp_path.drive
    with isolated_dirs(tmp_path.drive + "sandbox") as root:
        assert root == tmp_path / "sandbox"
        monkeypatch.chdir(second)
        actual = platformdirs.PlatformDirs("app", ensure_exists=True).user_config_path
        assert actual == tmp_path / "sandbox/user_config/app"
        assert actual.is_dir()
        assert not (second / "sandbox").exists()
