"""PC Nightreign save container I/O.

Format knowledge (BND4 container, AES-128-CBC, MD5 slot checksums) is taken from
the MIT-licensed alfizari/Elden-Ring-Nightreign-Save-Editor (src/packer/_pc.py).

Design rules for this module:
  * Loading never modifies anything.
  * Only slots that were explicitly replaced are re-encrypted; every other
    byte of the file (including untouched slots' ciphertext) stays identical.
  * Writing is backup -> temp file -> fsync -> atomic replace.

Run `python save_io.py <NR0000.sl2>` for a read-only summary (slots, names, Murk).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DS2_KEY = b"\x18\xf6\x32\x66\x05\xbd\x17\x8a\x55\x24\x52\x3a\xc0\xa0\xc6\x09"

BND4_MAGIC = b"BND4"
BND4_ENTRY_MAGIC = b"\x40\x00\x00\x00\xff\xff\xff\xff"
BND4_HEADER_LEN = 64
BND4_ENTRY_HEADER_LEN = 32

IV_SIZE = 16
START_OF_CHECKSUM_DATA = 4          # MD5 covers payload[4 : len-28]
END_OF_CHECKSUM_DATA = 12 + 16      # 12 pad bytes + 16 byte MD5 at the very end
CHARACTER_SLOT_COUNT = 10
REGULATION_ENTRY = 10               # USERDATA_10 holds the slot-active flags

_SLOT_FLAGS_MAGIC = re.compile(b"'\x00\x00FACE")
_SLOT_FLAGS_OFFSET = -61


class SaveFormatError(Exception):
    """The file is not a PC Nightreign save we understand."""


@dataclass(frozen=True)
class Entry:
    index: int
    size: int           # includes the 16 byte IV
    data_offset: int

    @property
    def payload_size(self) -> int:
        return self.size - IV_SIZE


def _md5_region(payload: bytes | bytearray) -> bytes:
    end = len(payload) - END_OF_CHECKSUM_DATA
    return hashlib.md5(bytes(payload[START_OF_CHECKSUM_DATA:end]), usedforsecurity=False).digest()


def patch_checksum(payload: bytearray) -> None:
    end = len(payload) - END_OF_CHECKSUM_DATA
    payload[end:end + 16] = _md5_region(payload)


def checksum_ok(payload: bytes | bytearray) -> bool:
    end = len(payload) - END_OF_CHECKSUM_DATA
    return bytes(payload[end:end + 16]) == _md5_region(payload)


def _decrypt(iv: bytes, blob: bytes) -> bytearray:
    d = Cipher(algorithms.AES(DS2_KEY), modes.CBC(iv)).decryptor()
    return bytearray(d.update(blob) + d.finalize())


def _encrypt(payload: bytes | bytearray) -> bytes:
    iv = os.urandom(IV_SIZE)
    e = Cipher(algorithms.AES(DS2_KEY), modes.CBC(iv)).encryptor()
    return iv + e.update(bytes(payload)) + e.finalize()


class SaveFile:
    """A decrypted view of one NR0000.sl2 (or .co2) file."""

    def __init__(self, path: Path | None, raw: bytes):
        if raw[:4] != BND4_MAGIC:
            raise SaveFormatError("Not a BND4 container (PC Nightreign save expected).")
        self.path = path
        self.raw = bytes(raw)
        self.sha256 = hashlib.sha256(self.raw).hexdigest()
        self.entries = self._read_entries()
        self.payloads: dict[int, bytearray] = {}
        for e in self.entries:
            blob = self.raw[e.data_offset:e.data_offset + e.size]
            if e.payload_size <= END_OF_CHECKSUM_DATA or e.payload_size % 16:
                raise SaveFormatError(f"Entry {e.index} has an unexpected size ({e.size}).")
            self.payloads[e.index] = _decrypt(blob[:IV_SIZE], blob[IV_SIZE:])
        self._replaced: dict[int, bytearray] = {}

    @classmethod
    def load(cls, path: str | Path) -> "SaveFile":
        p = Path(path)
        return cls(p, p.read_bytes())

    # -- container ---------------------------------------------------------
    def _read_entries(self) -> list[Entry]:
        if len(self.raw) < BND4_HEADER_LEN:
            raise SaveFormatError("File too small.")
        count = struct.unpack_from("<i", self.raw, 12)[0]
        if not 0 < count < 64:
            raise SaveFormatError(f"Implausible BND4 entry count: {count}.")
        out = []
        for i in range(count):
            pos = BND4_HEADER_LEN + BND4_ENTRY_HEADER_LEN * i
            if self.raw[pos:pos + 8] != BND4_ENTRY_MAGIC:
                raise SaveFormatError(f"BND4 entry {i}: bad entry magic.")
            size, _, data_offset = struct.unpack_from("<iii", self.raw, pos + 8)
            if size <= 0 or data_offset <= 0 or data_offset + size > len(self.raw):
                raise SaveFormatError(f"BND4 entry {i}: out-of-bounds data.")
            out.append(Entry(i, size, data_offset))
        return out

    # -- slots -------------------------------------------------------------
    def active_slots(self) -> tuple[bool, ...]:
        """Which of USERDATA_0..9 hold a character (flags stored in USERDATA_10)."""
        reg = self.payloads.get(REGULATION_ENTRY)
        if reg is None:
            raise SaveFormatError("USERDATA_10 missing; cannot tell which slots are used.")
        buf = bytes(reg)
        for m in _SLOT_FLAGS_MAGIC.finditer(buf):
            off = m.start() + _SLOT_FLAGS_OFFSET
            if off < 0 or off + CHARACTER_SLOT_COUNT > len(buf):
                continue
            flags = struct.unpack_from("<10B", buf, off)
            if any(f not in (0, 1) for f in flags):
                continue
            return tuple(f == 1 for f in flags)
        raise SaveFormatError("Slot-flag marker not found in USERDATA_10.")

    def slot_data(self, index: int) -> bytearray:
        """Current decrypted payload (a copy) for the given entry."""
        return bytearray(self._replaced.get(index, self.payloads[index]))

    def checksum_ok(self, index: int) -> bool:
        """Checksum of the ORIGINAL payload as found on disk."""
        return checksum_ok(self.payloads[index])

    def replace_slot(self, index: int, new_payload: bytes | bytearray) -> None:
        if len(new_payload) != len(self.payloads[index]):
            raise ValueError("Slot size must not change (the game needs constant-size slots).")
        self._replaced[index] = bytearray(new_payload)

    # -- output ------------------------------------------------------------
    def to_bytes(self) -> bytes:
        out = bytearray(self.raw)
        for idx, payload in self._replaced.items():
            e = self.entries[idx]
            patched = bytearray(payload)
            patch_checksum(patched)
            blob = _encrypt(patched)
            assert len(blob) == e.size
            out[e.data_offset:e.data_offset + e.size] = blob
        assert len(out) == len(self.raw)
        return bytes(out)


# -- backup / atomic write ---------------------------------------------------
def make_backup(path: Path, backups_dir: Path) -> Path:
    """Copy `path` to backups_dir/<stem>_backup_YYYYMMDD_HHMMSS<suffix> and verify it."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = backups_dir / f"{path.stem}_backup_{stamp}{path.suffix}"
    n = 1
    while dest.exists():
        dest = backups_dir / f"{path.stem}_backup_{stamp}_{n}{path.suffix}"
        n += 1
    shutil.copy2(path, dest)
    if dest.read_bytes() != path.read_bytes():
        dest.unlink(missing_ok=True)
        raise OSError("Backup verification failed; nothing was written.")
    return dest


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def default_save_candidates() -> list[Path]:
    """PC save files under %APPDATA%/Nightreign/<SteamID>/, newest first."""
    base = os.environ.get("APPDATA")
    if not base:
        return []
    root = Path(base) / "Nightreign"
    found = [p for pat in ("NR0000.sl2", "NR0000.co2") for p in root.glob(f"*/{pat}")]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def main(argv: list[str]) -> int:
    """Read-only summary of a save (workflow step 2)."""
    if len(argv) != 2:
        print("usage: python save_io.py <NR0000.sl2>")
        return 2
    import relic_parser  # local import: save_io itself has no slot-layout knowledge

    sf = SaveFile.load(argv[1])
    print(f"{argv[1]}: {len(sf.entries)} entries, sha256 {sf.sha256[:16]}...")
    active = sf.active_slots()
    for i in range(CHARACTER_SLOT_COUNT):
        if not active[i]:
            continue
        ok = sf.checksum_ok(i)
        try:
            s = relic_parser.Slot(sf.slot_data(i))
            print(f"  slot {i}: name={s.name!r} murk={s.murks} relics={len(s.relics)} checksum={'ok' if ok else 'MISMATCH'}")
        except relic_parser.LayoutError as exc:
            print(f"  slot {i}: could not parse ({exc}) checksum={'ok' if ok else 'MISMATCH'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
