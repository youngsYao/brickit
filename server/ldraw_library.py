"""Install a manifest-pinned official LDraw snapshot using only the stdlib.

Downloads are verified before a single archive member is extracted. Installation
uses a sibling staging directory and a process-independent lock; an existing
library is never upgraded or overwritten implicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Union
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "ldraw-library.json"
DEFAULT_LIBRARY = Path.home() / ".cache" / "brick-it" / "ldraw"
MARKER_NAME = ".brickit-library.json"
LOCK_TIMEOUT_SECONDS = 120
LOCK_POLL_SECONDS = .2
CHUNK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_COUNT = 100_000
PathLike = Union[str, Path]


class LibrarySetupError(RuntimeError):
    """The pinned library could not be checked or installed without data loss."""


def default_library_path() -> Path:
    configured = os.environ.get("LDRAW_LIBRARY_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_LIBRARY


def _relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or ":" in value or "\x00" in value:
        raise LibrarySetupError("Manifest contains an unsafe relative path.")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise LibrarySetupError("Manifest contains a path traversal or ambiguous path.")
    return PurePosixPath(value)


def _load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LibrarySetupError("Cannot read ldraw-library.json. Restore the project manifest and retry.") from exc
    if not isinstance(manifest, dict):
        raise LibrarySetupError("The library manifest must be a JSON object.")
    for field in ("version", "releaseTag", "rootDirectory"):
        if not isinstance(manifest.get(field), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", manifest[field]):
            raise LibrarySetupError("Invalid manifest field: " + field)
    if manifest["rootDirectory"] in {".", ".."}:
        raise LibrarySetupError("Invalid archive root directory.")
    digest = manifest.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise LibrarySetupError("The manifest must contain an exact SHA-256 digest.")
    manifest["sha256"] = digest.lower()
    for field, maximum in (("archiveBytes", MAX_ARCHIVE_BYTES), ("extractedBytes", MAX_EXTRACTED_BYTES), ("fileCount", MAX_FILE_COUNT)):
        value = manifest.get(field)
        if type(value) is not int or not 1 <= value <= maximum:
            raise LibrarySetupError("Invalid or excessive manifest resource limit: " + field)
    try:
        if not isinstance(manifest.get("url"), str):
            raise ValueError("URL must be a string")
        url = urlsplit(manifest["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("https URL required")
        url.port
    except (ValueError, TypeError) as exc:
        raise LibrarySetupError("The pinned release URL must use HTTPS without embedded credentials.") from exc
    for field in ("requiredFiles", "licenseFiles"):
        entries = manifest.get(field)
        if not isinstance(entries, list) or not entries:
            raise LibrarySetupError("The manifest must list " + field)
        seen = set()
        for entry in entries:
            normalized = str(_relative_path(entry)).casefold()
            if normalized in seen:
                raise LibrarySetupError("Duplicate path in manifest " + field)
            seen.add(normalized)
    supplementary = manifest.get("supplementaryLicenseFiles", [])
    if not isinstance(supplementary, list):
        raise LibrarySetupError("supplementaryLicenseFiles must be a list.")
    seen = set()
    for item in supplementary:
        if not isinstance(item, dict):
            raise LibrarySetupError("Invalid supplementary license entry.")
        _relative_path(item.get("source", ""))
        target = _relative_path(item.get("target", ""))
        digest = item.get("sha256", "")
        if len(target.parts) != 1 or target.name.casefold() == MARKER_NAME.casefold() or target.name.casefold() in seen:
            raise LibrarySetupError("Supplementary license target must be a unique safe basename.")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise LibrarySetupError("Supplementary licenses require an exact SHA-256 digest.")
        item["sha256"] = digest.lower()
        seen.add(target.name.casefold())
    return manifest


def _structure_valid(path: Path, manifest: dict) -> bool:
    if not path.is_dir() or not (path / "parts").is_dir() or not (path / "p").is_dir():
        return False
    for item in set(manifest["requiredFiles"] + manifest["licenseFiles"]):
        required = path.joinpath(*PurePosixPath(item).parts)
        if not required.is_file() or required.stat().st_size == 0:
            return False
    return True


def _check_existing(destination: Path, manifest: dict, log: Optional[Callable[[str], None]]) -> bool:
    marker = destination / MARKER_NAME
    if marker.is_symlink():
        raise LibrarySetupError("The managed library marker cannot be a symbolic link. Existing files were not changed.")
    if marker.exists():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LibrarySetupError("The existing managed library marker is invalid. Choose a new destination; existing files were not changed.") from exc
        if not isinstance(metadata, dict) or metadata.get("version") != manifest["version"] or metadata.get("sha256") != manifest["sha256"]:
            raise LibrarySetupError("The managed library version/SHA-256 does not match this manifest. Choose a new destination; automatic upgrades are disabled.")
        if not _structure_valid(destination, manifest):
            raise LibrarySetupError("The managed library is incomplete. Choose a new destination or restore it manually; existing files were not changed.")
        if log:
            log("LDraw is ready (mode=managed, version=" + manifest["version"] + ").")
        return True
    if _structure_valid(destination, manifest):
        if log:
            log("Using the existing LDraw library (mode=existing); no files or markers were changed.")
        return True
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise LibrarySetupError("The destination is non-empty and is not a valid LDraw library. Choose another --destination; existing files were not changed.")
    if destination.is_symlink():
        raise LibrarySetupError("The destination is an invalid symbolic link. Choose a different destination.")
    return False


@contextmanager
def _install_lock(destination: Path):
    lock = destination.parent / ("." + destination.name + ".install.lock")
    token = uuid.uuid4().hex
    started = time.monotonic()
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            if time.monotonic() - started >= LOCK_TIMEOUT_SECONDS:
                raise LibrarySetupError("Timed out waiting for another LDraw installation. Check the installer before removing its lock: " + str(lock))
            time.sleep(LOCK_POLL_SECONDS)
    owner_written = False
    try:
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "token": token, "started": time.time()}), encoding="utf-8")
        owner_written = True
        yield
    finally:
        # Never remove a lock whose ownership has changed behind our back.
        try:
            owner = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            owns_lock = owner.get("token") == token
        except (OSError, ValueError):
            owns_lock = not owner_written
        if owns_lock:
            shutil.rmtree(lock)


def _copy_verified(source, archive: Path, manifest: dict) -> None:
    total = 0
    digest = hashlib.sha256()
    with archive.open("wb") as target:
        while True:
            block = source.read(CHUNK_BYTES)
            if not block:
                break
            total += len(block)
            if total > manifest["archiveBytes"]:
                raise LibrarySetupError("Archive byte size exceeds the pinned archiveBytes limit.")
            digest.update(block)
            target.write(block)
    if total != manifest["archiveBytes"]:
        raise LibrarySetupError("Archive byte size does not match archiveBytes; the download is incomplete or has changed.")
    if digest.hexdigest() != manifest["sha256"]:
        raise LibrarySetupError("Archive SHA-256 mismatch. No library files were installed.")


def _obtain_archive(archive: Path, manifest: dict, archive_path: Optional[Path], download: bool, log: Optional[Callable[[str], None]]) -> None:
    if archive_path is not None:
        if log:
            log("Verifying the supplied local LDraw archive.")
        try:
            with archive_path.open("rb") as source:
                _copy_verified(source, archive, manifest)
        except OSError as exc:
            raise LibrarySetupError("Cannot read the supplied local archive.") from exc
        return
    if not download:
        raise LibrarySetupError("LDraw is not installed. Run python3 scripts/download_ldraw.py to install the pinned library.")
    if log:
        log("Downloading the pinned LDraw release (" + str(manifest["archiveBytes"]) + " bytes).")
    request = Request(manifest["url"], headers={"User-Agent": "brick-it-library-installer/1", "Accept": "application/octet-stream"})
    try:
        with urlopen(request, timeout=30) as source:
            final_url = source.geturl() if hasattr(source, "geturl") else manifest["url"]
            if urlsplit(final_url).scheme != "https":
                raise LibrarySetupError("The release download redirected to a non-HTTPS URL.")
            _copy_verified(source, archive, manifest)
    except LibrarySetupError:
        raise
    except (OSError, ValueError) as exc:
        raise LibrarySetupError("Could not download the pinned GitHub release. Check the network and retry the setup command; there is no upstream fallback.") from exc


def _validated_members(archive: zipfile.ZipFile, manifest: dict) -> list:
    members = archive.infolist()
    if len(members) > min(MAX_FILE_COUNT * 2, manifest["fileCount"] + 10000):
        raise LibrarySetupError("ZIP contains too many directory or file entries.")
    root_name = manifest["rootDirectory"]
    seen = set()
    files = 0
    expanded = 0
    output = []
    for member in members:
        original = member.orig_filename
        name = original[:-1] if original.endswith("/") else original
        path = _relative_path(name)
        if path.parts[0] != root_name or (len(path.parts) == 1 and not member.is_dir()):
            raise LibrarySetupError("ZIP contains files outside its declared root directory.")
        normalized = str(path).casefold()
        if normalized in seen:
            raise LibrarySetupError("ZIP contains duplicate or case-ambiguous normalized paths.")
        seen.add(normalized)
        if len(path.parts) == 2 and path.name.casefold() == MARKER_NAME.casefold():
            raise LibrarySetupError("ZIP contains a reserved installation marker.")
        mode = (member.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode):
            raise LibrarySetupError("ZIP symbolic links are not allowed.")
        if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise LibrarySetupError("ZIP special filesystem entries are not allowed.")
        if kind == stat.S_IFDIR and not member.is_dir():
            raise LibrarySetupError("ZIP directory metadata does not match its path.")
        if member.flag_bits & 1:
            raise LibrarySetupError("Encrypted ZIP entries are not allowed.")
        if member.file_size < 0 or member.compress_size < 0:
            raise LibrarySetupError("ZIP entry has an invalid byte size.")
        if not member.is_dir():
            files += 1
            expanded += member.file_size
            if files > manifest["fileCount"] or expanded > manifest["extractedBytes"]:
                raise LibrarySetupError("ZIP exceeds manifest fileCount or expanded byte limits.")
        output.append((member, path))
    if files != manifest["fileCount"] or expanded != manifest["extractedBytes"]:
        raise LibrarySetupError("ZIP file count or extracted byte size does not match the manifest.")
    return output


def _extract_verified(archive_path: Path, stage: Path, manifest: dict) -> Path:
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = _validated_members(archive, manifest)
        for member, relative in members:
            target = stage.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            # Files are created exclusively in a fresh tree; no existing local
            # file can be overwritten through archive metadata or symlinks.
            with archive.open(member, "r") as source, target.open("xb") as output:
                while True:
                    block = source.read(CHUNK_BYTES)
                    if not block:
                        break
                    count += len(block)
                    total += len(block)
                    if count > member.file_size or total > manifest["extractedBytes"]:
                        raise LibrarySetupError("ZIP extraction exceeds its expanded byte limits.")
                    output.write(block)
            if count != member.file_size:
                raise LibrarySetupError("ZIP member extraction was incomplete.")
    result = stage / manifest["rootDirectory"]
    if total != manifest["extractedBytes"] or not _structure_valid(result, manifest):
        raise LibrarySetupError("Extracted library is missing required files, parts/primitives directories, or license files.")
    return result


def _supplementary_licenses(extracted: Path, manifest: dict) -> None:
    """Add repository-pinned notices after checking the original archive totals."""
    for item in manifest.get("supplementaryLicenseFiles", []):
        source = ROOT.joinpath(*PurePosixPath(item["source"]).parts).resolve()
        if not source.is_relative_to(ROOT.resolve()) or not source.is_file():
            raise LibrarySetupError("The supplementary license source must be a file inside the project repository.")
        if source.stat().st_size > 1024 * 1024:
            raise LibrarySetupError("The supplementary license file exceeds its byte limit.")
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise LibrarySetupError("Supplementary license SHA-256 mismatch; no library was installed.")
        target = extracted / item["target"]
        if target.exists():
            if not target.is_file() or target.read_bytes() != content:
                raise LibrarySetupError("A supplementary license would replace a different archive file.")
        else:
            with target.open("xb") as output:
                output.write(content)


def ensure_ldraw_library(*, library_path: Optional[PathLike] = None,
                         manifest_path: Optional[PathLike] = None,
                         archive_path: Optional[PathLike] = None,
                         download: bool = True,
                         log: Optional[Callable[[str], None]] = None) -> Path:
    """Return a usable library, installing only into a new or empty destination.

    ``download=False`` is strictly an existing-library check unless an explicit
    local ``archive_path`` is supplied. Callers implementing ``--check`` should
    omit ``archive_path``. Existing unmarked libraries are adopted read-only;
    managed snapshots with a mismatched marker fail without an implicit upgrade.
    """
    manifest = _load_manifest(Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST)
    destination = (Path(library_path) if library_path is not None else default_library_path()).expanduser().absolute()
    try:
        if _check_existing(destination, manifest, log):
            return destination.resolve()
        if not download and archive_path is None:
            raise LibrarySetupError("LDraw is not installed. Run python3 scripts/download_ldraw.py; --check does not download files.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _install_lock(destination):
            # Another installer may have completed while this one waited.
            if _check_existing(destination, manifest, log):
                return destination.resolve()
            with tempfile.TemporaryDirectory(prefix="." + destination.name + ".stage-", dir=str(destination.parent)) as temp:
                stage = Path(temp)
                archive = stage / "snapshot.zip"
                _obtain_archive(archive, manifest, Path(archive_path).expanduser() if archive_path is not None else None, download, log)
                if log:
                    log("Archive verified. Extracting the pinned library into a temporary directory.")
                extracted = _extract_verified(archive, stage, manifest)
                _supplementary_licenses(extracted, manifest)
                marker = {"version": manifest["version"], "sha256": manifest["sha256"],
                          "releaseTag": manifest["releaseTag"], "mode": "managed",
                          "archiveBytes": manifest["archiveBytes"], "extractedBytes": manifest["extractedBytes"],
                          "fileCount": manifest["fileCount"], "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                (extracted / MARKER_NAME).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
                # Recheck under the lock immediately before publishing. Refuse
                # even an unexpected new file instead of destroying local data.
                if _check_existing(destination, manifest, None):
                    return destination.resolve()
                if destination.exists():
                    destination.rmdir()  # only an empty directory reaches here
                os.replace(str(extracted), str(destination))
                if log:
                    log("LDraw installed successfully (mode=managed, version=" + manifest["version"] + ").")
                return destination.resolve()
    except LibrarySetupError:
        raise
    except Exception as exc:
        raise LibrarySetupError("LDraw setup failed while checking or extracting the archive. Existing library files were not changed; retry or choose a new destination.") from exc
