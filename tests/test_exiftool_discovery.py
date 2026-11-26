"""ExifTool discovery must not encode one developer's disk layout."""

from __future__ import annotations

from pathlib import Path

from iPhoto.utils import exiftool


def test_conda_roots_come_from_the_environment_and_the_conda_registry(
    tmp_path: Path, monkeypatch
) -> None:
    active_env = tmp_path / "conda-env"
    registered_env = tmp_path / "registered-env"
    home = tmp_path / "home"
    (home / ".conda").mkdir(parents=True)
    (home / ".conda" / "environments.txt").write_text(
        f"{registered_env}\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CONDA_PREFIX", str(active_env))

    roots = exiftool._conda_roots()

    assert active_env in roots
    assert registered_env in roots


def test_no_drive_letters_are_hardcoded_in_the_candidate_list() -> None:
    source = Path(exiftool.__file__).read_text(encoding="utf-8")

    assert 'Path("D:/' not in source
    assert "Path('D:/" not in source
    assert "Path(\"C:/ProgramData/miniconda3\")" not in source
