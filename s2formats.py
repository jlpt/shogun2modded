"""Readers and writers for the Shogun 2 file formats the mod builder touches.

- Pack containers (PFH0 / PFH2 / PFH3), the .pack files in the game's data folder.
- DB tables (db/<table>_tables/<file>), binary tables decoded with a column layout.
- LOC files (text/**.loc), the localised strings shown in game.

The layouts follow RPFM (https://github.com/Frodo45127/rpfm), the community pack
editor. Everything is little-endian.
"""
import os
import struct
import time

PACK_TYPE_BOOT = 0
PACK_TYPE_RELEASE = 1
PACK_TYPE_PATCH = 2
PACK_TYPE_MOD = 3
PACK_TYPE_MOVIE = 4
PACK_TYPE_NAMES = {0: "boot", 1: "release", 2: "patch", 3: "mod", 4: "movie"}

# Bits of the pack type field above the low nibble.
FLAG_HAS_EXTENDED_HEADER = 0x100
FLAG_HAS_ENCRYPTED_INDEX = 0x80
FLAG_HAS_INDEX_WITH_TIMESTAMPS = 0x40
FLAG_HAS_ENCRYPTED_DATA = 0x10

# Seconds between 1601-01-01 (Windows FILETIME epoch) and 1970-01-01.
FILETIME_EPOCH_OFFSET = 11644473600


class FormatError(Exception):
    pass


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------

class PackEntry(object):
    def __init__(self, path, offset, size):
        self.path = path
        self.offset = offset
        self.size = size


class Pack(object):
    """A pack on disk. Only the index is read up front; file data is read on demand."""

    def __init__(self, disk_path):
        self.disk_path = disk_path
        self.name = os.path.basename(disk_path)
        with open(disk_path, "rb") as f:
            header = f.read(32)
            if len(header) < 24:
                raise FormatError("%s: too small to be a pack" % self.name)

            magic = header[:4]
            if magic not in (b"PFH0", b"PFH2", b"PFH3"):
                raise FormatError("%s: unsupported pack version %r" % (self.name, magic))
            self.version = magic.decode("ascii")

            type_and_flags, dep_count, dep_size, file_count, index_size = struct.unpack_from("<5I", header, 4)
            self.type = type_and_flags & 0xF
            self.flags = type_and_flags & ~0xF
            if self.type not in PACK_TYPE_NAMES:
                raise FormatError("%s: unknown pack type %d" % (self.name, self.type))
            if self.flags & (FLAG_HAS_EXTENDED_HEADER | FLAG_HAS_ENCRYPTED_INDEX | FLAG_HAS_ENCRYPTED_DATA):
                raise FormatError("%s: encrypted/extended packs are not supported" % self.name)

            # PFH2 and PFH3 add a u64 FILETIME to the header.
            header_size = 24 if self.version == "PFH0" else 32
            f.seek(header_size)
            f.read(dep_size)  # Dependency list: null-terminated pack names. Not needed.
            index = f.read(index_size)
            if len(index) != index_size:
                raise FormatError("%s: truncated file index" % self.name)

        index_timestamps = self.version != "PFH0" and self.flags & FLAG_HAS_INDEX_WITH_TIMESTAMPS
        self.entries = {}
        data_pos = header_size + dep_size + index_size
        pos = 0
        for _ in range(file_count):
            (size,) = struct.unpack_from("<I", index, pos)
            pos += 4
            if index_timestamps:
                pos += 8
            end = index.index(b"\0", pos)
            path = index[pos:end].decode("utf-8", "replace").replace("\\", "/")
            pos = end + 1
            self.entries[path.lower()] = PackEntry(path, data_pos, size)
            data_pos += size

        if data_pos != os.path.getsize(disk_path):
            raise FormatError("%s: index does not match file size" % self.name)

    @property
    def type_name(self):
        return PACK_TYPE_NAMES[self.type]

    def read(self, entry):
        with open(self.disk_path, "rb") as f:
            f.seek(entry.offset)
            return f.read(entry.size)


def filetime_now():
    return int((time.time() + FILETIME_EPOCH_OFFSET) * 10000000)


def encode_pack(files, pack_type):
    """Build a pack from a {path: bytes} dict.

    Mod packs are written as PFH3 and everything else as PFH2, matching what RPFM
    writes for Shogun 2. Both versions share the same layout.
    """
    magic = b"PFH3" if pack_type == PACK_TYPE_MOD else b"PFH2"
    paths = sorted(files, key=lambda p: p.lower())

    index = b"".join(
        struct.pack("<I", len(files[path])) + path.replace("/", "\\").encode("utf-8") + b"\0"
        for path in paths
    )
    header = magic + struct.pack("<5IQ", pack_type, 0, 0, len(paths), len(index), filetime_now())
    return header + index + b"".join(files[path] for path in paths)


# ---------------------------------------------------------------------------
# DB tables
# ---------------------------------------------------------------------------

DB_GUID_MARKER = b"\xfd\xfe\xfc\xff"
DB_VERSION_MARKER = b"\xfc\xfd\xfe\xff"

FIXED_SIZE_TYPES = {
    "I16": ("<h", 2),
    "I32": ("<i", 4),
    "I64": ("<q", 8),
    "F32": ("<f", 4),
    "F64": ("<d", 8),
}


class DbHeader(object):
    def __init__(self, guid, version, entry_count, size):
        self.guid = guid
        self.version = version
        self.entry_count = entry_count
        self.size = size


def read_db_header(data):
    pos = 0
    guid = ""
    if data[pos:pos + 4] == DB_GUID_MARKER:
        (length,) = struct.unpack_from("<H", data, pos + 4)
        guid = data[pos + 6:pos + 6 + length * 2].decode("utf-16-le")
        pos += 6 + length * 2

    version = 0
    if data[pos:pos + 4] == DB_VERSION_MARKER:
        (version,) = struct.unpack_from("<i", data, pos + 4)
        pos += 8

    if len(data) < pos + 5:
        raise FormatError("DB header truncated")
    # One unknown byte (always 1), then the row count.
    (entry_count,) = struct.unpack_from("<I", data, pos + 1)
    return DbHeader(guid, version, entry_count, pos + 5)


class Cell(object):
    """A decoded value plus where it lives, so numbers can be patched in place."""

    def __init__(self, field_type, offset, value):
        self.field_type = field_type
        self.offset = offset
        self.value = value


def _read_string(data, pos, wide):
    (length,) = struct.unpack_from("<H", data, pos)
    pos += 2
    size = length * 2 if wide else length
    raw = data[pos:pos + size]
    if len(raw) != size:
        raise FormatError("string runs past end of table")
    return raw.decode("utf-16-le" if wide else "utf-8", "replace"), pos + size


def _read_bool(data, pos):
    if pos >= len(data) or data[pos] not in (0, 1):
        raise FormatError("invalid boolean")
    return data[pos] == 1, pos + 1


def decode_rows(data, header, fields):
    """Decode every row with one column layout. Raises FormatError if it does not fit exactly."""
    rows = []
    pos = header.size
    for _ in range(header.entry_count):
        row = {}
        for name, field_type in fields:
            start = pos
            if field_type in FIXED_SIZE_TYPES:
                fmt, size = FIXED_SIZE_TYPES[field_type]
                if pos + size > len(data):
                    raise FormatError("row runs past end of table")
                (value,) = struct.unpack_from(fmt, data, pos)
                pos += size
            elif field_type == "Boolean":
                value, pos = _read_bool(data, pos)
            elif field_type in ("StringU8", "StringU16"):
                value, pos = _read_string(data, pos, field_type == "StringU16")
            elif field_type in ("OptionalStringU8", "OptionalStringU16"):
                present, pos = _read_bool(data, pos)
                value = ""
                if present:
                    value, pos = _read_string(data, pos, field_type == "OptionalStringU16")
            else:
                raise FormatError("unsupported column type %s" % field_type)
            row[name] = Cell(field_type, start, value)
        rows.append(row)

    if pos != len(data):
        raise FormatError("layout leaves %d unread bytes" % (len(data) - pos))
    return rows


def decode_db(data, definitions):
    """Decode a table using whichever definition for its version fits the data exactly.

    Returns (header, definition, rows). Tables without a version marker are version 0,
    and RPFM stores alternative version-0 layouts as negative versions, so those are
    tried too.
    """
    header = read_db_header(data)
    if header.version == 0:
        candidates = [d for d in definitions if d["version"] < 1]
    else:
        candidates = [d for d in definitions if d["version"] == header.version]

    for definition in candidates:
        try:
            return header, definition, decode_rows(data, header, definition["fields"])
        except (FormatError, struct.error, UnicodeDecodeError):
            continue
    raise FormatError("no known layout for table version %d" % header.version)


# ---------------------------------------------------------------------------
# LOC files
# ---------------------------------------------------------------------------

LOC_MAGIC = b"\xff\xfeLOC\x00"


class LocEntry(object):
    def __init__(self, key, text, tooltip):
        self.key = key
        self.text = text
        self.tooltip = tooltip


def decode_loc(data):
    if data[:6] != LOC_MAGIC or len(data) < 14:
        raise FormatError("not a LOC file")
    version, count = struct.unpack_from("<iI", data, 6)
    pos = 14
    entries = []
    for _ in range(count):
        key, pos = _read_string(data, pos, True)
        text, pos = _read_string(data, pos, True)
        tooltip, pos = _read_bool(data, pos)
        entries.append(LocEntry(key, text, tooltip))
    if pos != len(data):
        raise FormatError("LOC file has %d trailing bytes" % (len(data) - pos))
    return version, entries


def _encode_string_u16(text):
    raw = text.encode("utf-16-le")
    return struct.pack("<H", len(raw) // 2) + raw


def encode_loc(version, entries):
    out = [LOC_MAGIC, struct.pack("<iI", version, len(entries))]
    for entry in entries:
        out.append(_encode_string_u16(entry.key))
        out.append(_encode_string_u16(entry.text))
        out.append(b"\x01" if entry.tooltip else b"\x00")
    return b"".join(out)
