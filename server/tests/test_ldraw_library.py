"""Pinned-library bootstrap safety and recovery, with no external network access.

Every archive is a tiny test fixture. Corrupt/hostile archives get a matching
checksum first, so extraction checks cannot accidentally pass behind a hash error.
"""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import stat
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest

from server import ldraw_library as library


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ["LDConfig.ldr", "parts/3001.dat", "p/stud.dat"]
LICENSES = ["Readme.txt", "CAreadme.txt", "license.txt"]
CONTENTS = {
    "LDConfig.ldr": b"0 !COLOUR Red CODE 4 VALUE #C91A09 EDGE #333333\n",
    "parts/3001.dat": b"0 Brick 2 x 4\n0 !LDRAW_ORG Part\n",
    "p/stud.dat": b"0 Stud\n0 !LDRAW_ORG Primitive\n",
    "Readme.txt": b"Test fixture library readme\n",
    "CAreadme.txt": b"Test fixture contributor agreement\n",
    "license.txt": b"Test fixture license notice\n",
}


def archive_bytes(entries=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries or [(f"ldraw/{name}", data) for name, data in CONTENTS.items()]:
            archive.writestr(name, content)
    return output.getvalue()


def fixture_manifest(tmp_path, data=None, **overrides):
    data = archive_bytes() if data is None else data
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            count, size = len(files), sum(info.file_size for info in files)
    except zipfile.BadZipFile:
        count, size = len(CONTENTS), sum(map(len, CONTENTS.values()))
    manifest = {
        "version": "test-library-1",
        "releaseTag": "test-library-1",
        "url": "https://github.com/youngsYao/brickit/releases/download/test-library-1/library.zip",
        "sha256": hashlib.sha256(data).hexdigest(),
        "archiveBytes": len(data), "extractedBytes": size, "fileCount": count,
        "upstreamUrl": "https://library.ldraw.org/library/updates/complete.zip",
        "rootDirectory": "ldraw", "requiredFiles": REQUIRED, "licenseFiles": LICENSES,
    }
    manifest.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def install_fixture(target):
    for name, data in CONTENTS.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return target


def snapshot(path):
    return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}


class StreamResponse(io.BytesIO):
    """An HTTP-like response that forbids an unbounded .read()."""

    def __init__(self, data, gate=None, entered=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self.status = 200
        self.read_sizes = []
        self.gate, self.entered = gate, entered

    def read(self, size=-1):
        assert 0 < size <= 4 * 1024 * 1024, "Download must stream bounded chunks"
        self.read_sizes.append(size)
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(5), "Concurrent fixture was never released"
        return super().read(size)

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("This test must never access the network")
    monkeypatch.setattr(library, "urlopen", forbidden)
    # Isolate destination selection from the developer's real library setting.
    monkeypatch.delenv("LDRAW_LIBRARY_PATH", raising=False)


def test_streamed_download_verifies_pin_and_installs_atomically(tmp_path, monkeypatch):
    data = archive_bytes()
    manifest_path, manifest = fixture_manifest(tmp_path, data)
    target = tmp_path / "install" / "ldraw"
    responses, requests, logs = [], [], []

    def serve(request, *args, **kwargs):
        requests.append(request.full_url if hasattr(request, "full_url") else request)
        response = StreamResponse(data); responses.append(response); return response

    monkeypatch.setattr(library, "urlopen", serve)
    actual = library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, log=logs.append)
    assert actual == target.resolve()
    assert requests == [manifest["url"]]
    assert responses[0].read_sizes and responses[0].closed
    for filename, content in CONTENTS.items():
        assert (target / filename).read_bytes() == content
    marker = json.loads((target / ".brickit-library.json").read_text())
    assert marker["version"] == manifest["version"]
    assert marker["sha256"] == manifest["sha256"]
    assert marker["mode"] == "managed"
    assert list(target.parent.iterdir()) == [target], "Lock and temporary files must be cleaned up"


def test_pinned_local_archive_can_install_fully_offline(tmp_path):
    data = archive_bytes(); manifest_path, _ = fixture_manifest(tmp_path, data)
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"
    result = library.ensure_ldraw_library(library_path=str(target), manifest_path=str(manifest_path), archive_path=str(archive))
    assert result == target.resolve()
    assert (target / "parts/3001.dat").read_bytes() == CONTENTS["parts/3001.dat"]
    assert archive.read_bytes() == data, "An explicit user-provided archive is not consumed"


@pytest.mark.parametrize("managed", [False, True])
def test_usable_existing_library_is_reused_without_network_or_rewrites(tmp_path, managed):
    manifest_path, manifest = fixture_manifest(tmp_path)
    target = install_fixture(tmp_path / "existing")
    if managed:
        (target / ".brickit-library.json").write_text(json.dumps({"version": manifest["version"], "sha256": manifest["sha256"], "mode": "managed"}))
    before = snapshot(target)
    assert library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path) == target.resolve()
    assert library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, download=False) == target.resolve()
    assert snapshot(target) == before


@pytest.mark.parametrize("field,value", [("version", "different-version"), ("sha256", "0" * 64)])
def test_stale_managed_library_is_not_silently_overwritten(tmp_path, field, value):
    manifest_path, manifest = fixture_manifest(tmp_path)
    target = install_fixture(tmp_path / "existing")
    marker = {"version": manifest["version"], "sha256": manifest["sha256"], "mode": "managed", field: value}
    (target / ".brickit-library.json").write_text(json.dumps(marker))
    before = snapshot(target)
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)
    assert snapshot(target) == before


def test_incomplete_existing_directory_is_preserved_without_downloading(tmp_path):
    manifest_path, _ = fixture_manifest(tmp_path)
    target = tmp_path / "existing"; target.mkdir()
    (target / "my-custom-part.dat").write_bytes(b"Must not be lost")
    before = snapshot(target)
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)
    assert snapshot(target) == before


def test_check_missing_library_is_offline_and_does_not_create_a_target(tmp_path):
    manifest_path, _ = fixture_manifest(tmp_path)
    target = tmp_path / "missing" / "ldraw"
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, download=False)
    assert not target.exists()


@pytest.mark.parametrize("fault", ["sha256", "too-short", "too-long", "malformed-zip"])
def test_corrupt_download_is_rejected_and_staging_and_lock_are_removed(tmp_path, monkeypatch, fault):
    data = b"not a zip archive" if fault == "malformed-zip" else archive_bytes()
    overrides = {"sha256": "0" * 64} if fault == "sha256" else {"archiveBytes": len(data) + (1 if fault == "too-short" else -1)} if fault in {"too-short", "too-long"} else {}
    manifest_path, _ = fixture_manifest(tmp_path, data, **overrides)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    monkeypatch.setattr(library, "urlopen", lambda *args, **kwargs: StreamResponse(data))
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("member", [
    "ldraw/../escape.dat", "../escape.dat", "/absolute.dat",
    "C:/windows.dat", "C:\\windows.dat", "ldraw\\parts\\escape.dat",
    "ldraw/parts/../../escape.dat", "another-root/part.dat", "ldraw/parts//alias.dat",
])
def test_unsafe_archive_names_are_rejected_before_publication(tmp_path, member):
    entries = [(f"ldraw/{name}", content) for name, content in CONTENTS.items()]
    entries.append((member, b"must never be extracted"))
    data = archive_bytes(entries); manifest_path, _ = fixture_manifest(tmp_path, data)
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, archive_path=archive)
    assert list(target.parent.iterdir()) == []
    assert not (tmp_path / "escape.dat").exists()


def test_archive_symlink_is_rejected(tmp_path):
    link = zipfile.ZipInfo("ldraw/parts/link.dat")
    link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
    entries = [(f"ldraw/{name}", content) for name, content in CONTENTS.items()] + [(link, b"../../outside.dat")]
    data = archive_bytes(entries); manifest_path, _ = fixture_manifest(tmp_path, data)
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, archive_path=archive)
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("name", ["ldraw/parts/3001.dat", "ldraw/PARTS/3001.DAT"])
def test_duplicate_file_and_case_alias_cannot_overwrite_a_part(tmp_path, name):
    entries = [(f"ldraw/{path}", content) for path, content in CONTENTS.items()] + [(name, b"replaced")]
    with pytest.warns(UserWarning) if name == "ldraw/parts/3001.dat" else nullcontext():
        data = archive_bytes(entries)
    manifest_path, _ = fixture_manifest(tmp_path, data)
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, archive_path=archive)
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("missing", REQUIRED + LICENSES)
def test_required_geometry_and_all_license_notices_are_mandatory(tmp_path, missing):
    data = archive_bytes([(f"ldraw/{path}", content) for path, content in CONTENTS.items() if path != missing])
    manifest_path, _ = fixture_manifest(tmp_path, data)
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path, archive_path=archive)
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("field", ["extractedBytes", "fileCount"])
def test_manifest_expanded_size_and_file_count_must_match_the_archive(tmp_path, field):
    data = archive_bytes(); manifest_path, manifest = fixture_manifest(tmp_path, data)
    manifest[field] += 1; manifest_path.write_text(json.dumps(manifest))
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=tmp_path / "ldraw", manifest_path=manifest_path, archive_path=archive)
    assert not (tmp_path / "ldraw").exists()


def test_interrupted_stream_cleans_partial_files_and_a_retry_can_succeed(tmp_path, monkeypatch):
    data = archive_bytes(); manifest_path, _ = fixture_manifest(tmp_path, data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()

    class Interrupted(StreamResponse):
        def read(self, size=-1):
            if self.tell():
                raise OSError("Fixture connection was interrupted")
            return super().read(size)

    monkeypatch.setattr(library, "urlopen", lambda *args, **kwargs: Interrupted(data))
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)
    assert list(target.parent.iterdir()) == []
    monkeypatch.setattr(library, "urlopen", lambda *args, **kwargs: StreamResponse(data))
    assert library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path) == target.resolve()
    assert list(target.parent.iterdir()) == [target]


def test_concurrent_installers_share_one_download_and_leave_no_lock(tmp_path, monkeypatch):
    data = archive_bytes(); manifest_path, _ = fixture_manifest(tmp_path, data)
    target = tmp_path / "install" / "ldraw"
    gate, entered, second_started = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def serve(*args, **kwargs):
        calls.append(1); return StreamResponse(data, gate=gate, entered=entered)

    monkeypatch.setattr(library, "urlopen", serve)
    monkeypatch.setattr(library, "LOCK_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(library, "LOCK_POLL_SECONDS", .01)

    def second_install():
        second_started.set(); return library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(library.ensure_ldraw_library, library_path=target, manifest_path=manifest_path)
        try:
            assert entered.wait(2)
            second = pool.submit(second_install)
            assert second_started.wait(2)
        finally:
            gate.set()
        assert first.result(timeout=5) == target.resolve()
        assert second.result(timeout=5) == target.resolve()
    assert len(calls) == 1
    assert list(target.parent.iterdir()) == [target]


def test_foreign_lock_timeout_does_not_remove_someone_elses_lock(tmp_path, monkeypatch):
    manifest_path, _ = fixture_manifest(tmp_path)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    lock = target.parent / f".{target.name}.install.lock"; lock.mkdir()
    (lock / "owner.json").write_text(json.dumps({"pid": 999999, "testOwner": "foreign"}))
    before = snapshot(lock)
    monkeypatch.setattr(library, "LOCK_TIMEOUT_SECONDS", .02)
    monkeypatch.setattr(library, "LOCK_POLL_SECONDS", .001)
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=target, manifest_path=manifest_path)
    assert snapshot(lock) == before
    assert not target.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_cli_check_performs_no_network_and_has_meaningful_exit_status(tmp_path, monkeypatch, existing):
    manifest_path, _ = fixture_manifest(tmp_path)
    target = tmp_path / "ldraw"
    if existing:
        install_fixture(target)
    monkeypatch.setattr(sys, "argv", ["download_ldraw.py", "--check", "--manifest", str(manifest_path), "--library-path", str(target)])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(ROOT / "scripts/download_ldraw.py"), run_name="__main__")
    assert exited.value.code == (0 if existing else 1)
    assert target.exists() is existing


@pytest.fixture
def supplementary_fixture(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "docs/licenses/GPL-2.0.txt"
    source.parent.mkdir(parents=True)
    content = b"This is a test fixture for a repository-pinned supplementary GPL notice.\n"
    source.write_bytes(content)
    monkeypatch.setattr(library, "ROOT", repo)
    supplement = {"source": "docs/licenses/GPL-2.0.txt", "target": "COPYING-GPL-2.0.txt", "sha256": hashlib.sha256(content).hexdigest()}
    data = archive_bytes()
    manifest_path, manifest = fixture_manifest(tmp_path, data, supplementaryLicenseFiles=[supplement])
    archive = tmp_path / "library.zip"; archive.write_bytes(data)
    target = tmp_path / "install" / "ldraw"; target.parent.mkdir()
    return {"repo": repo, "source": source, "content": content, "manifest": manifest,
            "manifest_path": manifest_path, "archive": archive, "target": target}


def test_pinned_supplementary_license_is_added_without_changing_original_archive_files(supplementary_fixture):
    fixture = supplementary_fixture
    target = library.ensure_ldraw_library(library_path=fixture["target"], manifest_path=fixture["manifest_path"], archive_path=fixture["archive"])
    assert (target / "COPYING-GPL-2.0.txt").read_bytes() == fixture["content"]
    for name, content in CONTENTS.items():
        assert (target / name).read_bytes() == content
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("fault", ["sha", "source-traversal", "source-symlink", "target-traversal", "reserved-marker", "archive-collision"])
def test_invalid_supplementary_license_never_publishes_a_partial_library(supplementary_fixture, tmp_path, fault):
    fixture = supplementary_fixture
    supplement = fixture["manifest"]["supplementaryLicenseFiles"][0]
    if fault == "sha":
        fixture["source"].write_bytes(b"Changed after its digest was pinned")
    elif fault == "source-traversal":
        supplement["source"] = "../outside.txt"
    elif fault == "source-symlink":
        outside = tmp_path / "outside.txt"; outside.write_bytes(fixture["content"])
        fixture["source"].unlink(); fixture["source"].symlink_to(outside)
    elif fault == "target-traversal":
        supplement["target"] = "../outside.txt"
    elif fault == "reserved-marker":
        supplement["target"] = ".brickit-library.json"
    else:
        supplement["target"] = "Readme.txt"
    fixture["manifest_path"].write_text(json.dumps(fixture["manifest"]))
    with pytest.raises(library.LibrarySetupError):
        library.ensure_ldraw_library(library_path=fixture["target"], manifest_path=fixture["manifest_path"], archive_path=fixture["archive"])
    assert not fixture["target"].exists()
    assert list(fixture["target"].parent.iterdir()) == []


@pytest.mark.parametrize("managed", [False, True])
def test_reusing_an_existing_library_does_not_require_or_copy_supplementary_sources(supplementary_fixture, managed):
    fixture = supplementary_fixture
    target = install_fixture(fixture["target"])
    if managed:
        (target / ".brickit-library.json").write_text(json.dumps({"version": fixture["manifest"]["version"], "sha256": fixture["manifest"]["sha256"], "mode": "managed"}))
    before = snapshot(target)
    fixture["source"].unlink()
    assert library.ensure_ldraw_library(library_path=target, manifest_path=fixture["manifest_path"], download=False) == target.resolve()
    assert snapshot(target) == before
