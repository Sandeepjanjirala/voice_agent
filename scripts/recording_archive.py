"""Validate encrypted recording backups and stage only into an empty destination.

Run in the backend image with --network none. Plaintext exists only briefly in
memory for verification; recovery never overwrites an existing recording tree.
"""

import argparse
import json
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

MAX_ENCRYPTED_BYTES = 72 * 1024 * 1024
OBJECT_PATH = re.compile(r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+\.ogg\.enc")
DIRECTORY_PATH = re.compile(r"[a-zA-Z0-9_-]+")


class ArchiveError(ValueError):
    """Safe diagnostic without archive content, keys, or call identifiers."""


def inspect_archive(archive: Path, cipher: MultiFernet) -> list[str]:
    names = []
    seen = set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ArchiveError("Archive contains an unsafe path.")
            name = str(path)
            if member.isdir():
                if name != "." and not DIRECTORY_PATH.fullmatch(name):
                    raise ArchiveError("Unexpected recording directory.")
                continue
            if not member.isfile() or not OBJECT_PATH.fullmatch(name):
                raise ArchiveError(
                    "Archive contains a link, special file, or unexpected object."
                )
            if name in seen:
                raise ArchiveError("Archive contains duplicate objects.")
            if not 0 < member.size <= MAX_ENCRYPTED_BYTES:
                raise ArchiveError(
                    "Recording object exceeds the application's size limit."
                )
            encrypted = source.extractfile(member)
            if encrypted is None:
                raise ArchiveError("Recording object could not be read.")
            try:
                plaintext = cipher.decrypt(encrypted.read())
            except InvalidToken as error:
                raise ArchiveError(
                    "Recording cannot be decrypted with the preserved keys."
                ) from error
            if not plaintext.startswith(b"OggS") or len(plaintext) > 50 * 1024 * 1024:
                raise ArchiveError(
                    "Decrypted recording is not valid application audio."
                )
            del plaintext
            seen.add(name)
            names.append(name)
    return names


def recover(archive: Path, cipher: MultiFernet, destination: Path | None = None) -> int:
    # Validate every object before making any change to the empty staging volume.
    names = inspect_archive(archive, cipher)
    if destination is None:
        return len(names)
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or any(destination.iterdir())
    ):
        raise ArchiveError("Recovery destination must be an existing empty directory.")
    os.chmod(destination, 0o700)
    directories = set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            name = str(PurePosixPath(member.name))
            if not member.isfile():
                continue
            if name not in names:
                raise ArchiveError("Archive changed during recovery.")
            target = destination / name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            directories.add(target.parent)
            encrypted = source.extractfile(member)
            if encrypted is None:
                raise ArchiveError("Recording object could not be read.")
            with target.open("xb") as output:
                output.write(encrypted.read())
            os.chmod(target, 0o600)
            if os.name != "nt" and os.geteuid() == 0:
                os.chown(target, 10001, 10001)
    # Keep parent directories owned by the recovery process until all writes
    # finish, so the container needs CHOWN/FOWNER but not DAC_OVERRIDE.
    if os.name != "nt" and os.geteuid() == 0:
        for directory in directories:
            os.chown(directory, 10001, 10001)
        os.chown(destination, 10001, 10001)
    return len(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    # The same literal, non-executing env parser used for deployment configuration.
    from configure import ConfigError, read_env

    try:
        values = read_env(args.env_file)
        keys = values.get("RECORDING_ENCRYPTION_KEYS", "").split(",")
        cipher = MultiFernet(
            [Fernet(key.strip().encode()) for key in keys if key.strip()]
        )
        count = recover(args.archive, cipher, args.destination)
    except (ArchiveError, ConfigError, ValueError, OSError, tarfile.TarError):
        parser.exit(
            1,
            "Recording archive verification/recovery failed; check keys, archive integrity, paths, and an empty destination. No live recording volume was changed.\n",
        )
    print(
        json.dumps(
            {
                "ok": True,
                "verified_objects": count,
                "staged": args.destination is not None,
            }
        )
    )


if __name__ == "__main__":
    main()
