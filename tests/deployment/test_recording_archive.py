"""Synthetic encrypted recording recovery, without external audio/providers."""

import io
import tarfile

import pytest
from cryptography.fernet import Fernet, MultiFernet

from scripts.recording_archive import ArchiveError, recover


def make_archive(path, entries):
    with tarfile.open(path, "w:gz") as output:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data)
                output.addfile(info, io.BytesIO(data))
            else:
                info.linkname = "../outside"
                output.addfile(info)


@pytest.fixture
def cipher():
    return MultiFernet([Fernet(Fernet.generate_key())])


def test_verified_recording_stages_as_ciphertext_only(tmp_path, cipher):
    archive = tmp_path / "recordings.tar.gz"
    ciphertext = cipher.encrypt(b"OggS synthetic audio")
    make_archive(archive, [("./org-id/call-id.ogg.enc", ciphertext, tarfile.REGTYPE)])
    target = tmp_path / "recovered"
    target.mkdir()
    assert recover(archive, cipher) == 1
    assert list(target.iterdir()) == []
    assert recover(archive, cipher, target) == 1
    assert (target / "org-id/call-id.ogg.enc").read_bytes() == ciphertext
    assert b"OggS" not in (target / "org-id/call-id.ogg.enc").read_bytes()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("org-id/call-id.ogg.enc", tarfile.SYMTYPE),
        ("org-id/call-id.ogg.enc", tarfile.LNKTYPE),
        ("org-id/call-id.ogg.enc", tarfile.CHRTYPE),
        ("org-id/script.sh", tarfile.REGTYPE),
    ],
)
def test_unsafe_archive_never_writes_to_destination(tmp_path, cipher, name, kind):
    archive = tmp_path / "recordings.tar.gz"
    make_archive(archive, [(name, cipher.encrypt(b"OggS"), kind)])
    target = tmp_path / "recovered"
    target.mkdir()
    with pytest.raises(ArchiveError):
        recover(archive, cipher, target)
    assert list(target.iterdir()) == []


def test_wrong_keys_never_writes_any_recordings(tmp_path, cipher):
    archive = tmp_path / "recordings.tar.gz"
    other = Fernet(Fernet.generate_key())
    make_archive(
        archive,
        [
            ("org-id/first.ogg.enc", cipher.encrypt(b"OggS"), tarfile.REGTYPE),
            ("org-id/second.ogg.enc", other.encrypt(b"OggS"), tarfile.REGTYPE),
        ],
    )
    target = tmp_path / "recovered"
    target.mkdir()
    with pytest.raises(ArchiveError, match="preserved keys"):
        recover(archive, cipher, target)
    assert list(target.iterdir()) == []


def test_existing_recordings_are_never_overwritten(tmp_path, cipher):
    archive = tmp_path / "recordings.tar.gz"
    make_archive(
        archive, [("org-id/call.ogg.enc", cipher.encrypt(b"OggS"), tarfile.REGTYPE)]
    )
    target = tmp_path / "recovered"
    target.mkdir()
    existing = target / "keep.txt"
    existing.write_text("existing data")
    with pytest.raises(ArchiveError, match="empty directory"):
        recover(archive, cipher, target)
    assert existing.read_text() == "existing data"


def test_duplicate_archive_members_rejected(tmp_path, cipher):
    archive = tmp_path / "recordings.tar.gz"
    entry = ("org-id/call.ogg.enc", cipher.encrypt(b"OggS"), tarfile.REGTYPE)
    make_archive(archive, [entry, entry])
    with pytest.raises(ArchiveError, match="duplicate"):
        recover(archive, cipher)
