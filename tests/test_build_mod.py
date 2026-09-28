"""End-to-end tests against a fake Shogun 2 install built from the bundled table layouts.

Run with:  python -m unittest discover -s tests
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_mod  # noqa: E402
import s2formats as s2  # noqa: E402

with open(build_mod.SCHEMA_PATH, encoding="utf-8") as f:
    SCHEMAS = json.load(f)["tables"]


def definition(table, version):
    return [d for d in SCHEMAS[table] if d["version"] == version][0]["fields"]


def encode_string(text, wide):
    raw = text.encode("utf-16-le" if wide else "utf-8")
    return struct.pack("<H", len(raw) // 2 if wide else len(raw)) + raw


def encode_db(fields, rows, version, guid="0a1b2c3d-test"):
    out = bytearray()
    if guid is not None:
        out += s2.DB_GUID_MARKER + encode_string(guid, True)
    if version:
        out += s2.DB_VERSION_MARKER + struct.pack("<i", version)
    out += b"\x01" + struct.pack("<I", len(rows))
    for row in rows:
        for name, field_type in fields:
            value = row.get(name)
            if field_type in s2.FIXED_SIZE_TYPES:
                out += struct.pack(s2.FIXED_SIZE_TYPES[field_type][0], value or 0)
            elif field_type == "Boolean":
                out += b"\x01" if value else b"\x00"
            elif field_type in ("StringU8", "StringU16"):
                out += encode_string(value or "", field_type.endswith("U16"))
            elif field_type.startswith("OptionalString"):
                out += (b"\x01" + encode_string(value, field_type.endswith("U16"))) if value else b"\x00"
            else:
                raise AssertionError(field_type)
    return bytes(out)


def loc(entries):
    return s2.encode_loc(1, [s2.LocEntry(k, v, False) for k, v in entries])


def write_pack(path, files, pack_type):
    with open(path, "wb") as f:
        f.write(s2.encode_pack(files, pack_type))


FACTIONS = definition("factions_tables", 7)
UNIFORMS = definition("faction_uniform_colours_tables", 0)
BANNERS = definition("faction_banners_tables", 0)
UNIFORM_OVERRIDES = definition("uniform_to_faction_colours_tables", 2)


def faction_row(key, colour, scale, **extra):
    row = {"key": key, "subculture": "sc_shogun", "screen_name": key, "flags_path": "ui/flags/" + key,
           "rebel_colour_r": 1 * scale, "rebel_colour_g": 2 * scale, "rebel_colour_b": 3 * scale}
    for name, field_type in FACTIONS:
        if field_type == "F32" and "_colour_" in name and not name.startswith("rebel"):
            row[name] = colour
    row.update(extra)
    return row


def colour_row(key_column, key, value):
    row = {key_column: key}
    for name, field_type in UNIFORMS + BANNERS + UNIFORM_OVERRIDES:
        if field_type == "I32":
            row[name] = value
    return row


class FakeInstall(object):
    """A data folder shaped like Shogun 2's, with a release pack, a patch pack and language packs."""

    def __init__(self, root, factions_scale=255.0, factions_version=7):
        self.data_dir = os.path.join(root, "Total War SHOGUN 2", "data")
        os.makedirs(self.data_dir)
        with open(os.path.join(self.data_dir, "language.txt"), "w") as f:
            f.write("EN")

        s = factions_scale / 255.0
        release_factions = encode_db(FACTIONS, [faction_row("clan_shimazu", 10 * s, s)], factions_version)
        self.patched_factions = encode_db(FACTIONS, [
            faction_row("clan_shimazu", 50 * s, s, index=1),
            faction_row("clan_takeda", 60 * s, s, index=2),
            faction_row("rebel_clan_shimazu", 70 * s, s, index=3),
        ], factions_version)

        write_pack(os.path.join(self.data_dir, "data.pack"), {
            "db/factions_tables/factions": release_factions,
            "db/faction_uniform_colours_tables/faction_uniform_colours": encode_db(UNIFORMS, [
                colour_row("faction_name", "clan_shimazu", 11),
                colour_row("faction_name", "clan_takeda", 12),
            ], 0),
            "db/faction_banners_tables/faction_banners": encode_db(BANNERS, [
                dict(colour_row("key", "clan_shimazu", 13), symbol="ui/mon/shimazu"),
            ], 0),
            "db/uniform_to_faction_colours_tables/uniform_to_faction_colours": encode_db(UNIFORM_OVERRIDES, [
                dict(colour_row("faction_name", "clan_shimazu", 14), uniform_name="ashigaru", type="unit"),
                dict(colour_row("faction_name", "", 15), uniform_name="ashigaru", type="unit"),
            ], 2),
            "script/campaign/startpos.lua": b"-- untouched",
        }, s2.PACK_TYPE_RELEASE)

        write_pack(os.path.join(self.data_dir, "patch.pack"), {
            "db/factions_tables/factions": self.patched_factions,
        }, s2.PACK_TYPE_PATCH)

        write_pack(os.path.join(self.data_dir, "local_en.pack"), {
            "text/db/factions.loc": loc([
                ("factions_screen_name_clan_shimazu", "Shimazu"),
                ("factions_screen_adjective_clan_shimazu", "Shimazu"),
                ("factions_screen_name_when_shogun_clan_shimazu", "Shimazu Shogunate"),
                ("factions_screen_name_rebel_clan_shimazu", "Shimazu Rebels"),
                ("factions_screen_name_clan_takeda", "Takeda"),
            ]),
            "text/db/campaign.loc": loc([("campaign_intro", "The SHIMAZU of Satsuma")]),
        }, s2.PACK_TYPE_RELEASE)
        write_pack(os.path.join(self.data_dir, "local_ge.pack"), {
            "text/db/factions.loc": loc([("factions_screen_name_clan_shimazu", "Shimazu-Klan")]),
        }, s2.PACK_TYPE_RELEASE)

        # Someone else's mod: must never leak into the output.
        write_pack(os.path.join(self.data_dir, "aaa_other_mod.pack"), {
            "db/factions_tables/factions": b"not a real table",
        }, s2.PACK_TYPE_MOD)


class BuildModTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def run_tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = build_mod.main(list(argv))
        finally:
            sys.stdout, sys.stderr = old
        return code, out.getvalue(), err.getvalue()

    def build(self, install, *argv):
        code, out, err = self.run_tool("--game-dir", os.path.dirname(install.data_dir), *argv)
        self.assertEqual(code, 0, out + err)
        pack = s2.Pack(os.path.join(install.data_dir, "toshima_clan.pack"))
        return pack, out

    def table(self, pack, path, table):
        data = pack.read(pack.entries[path])
        _, _, rows = s2.decode_db(data, SCHEMAS[table])
        return data, {row[build_mod.faction_key_column(row)].value: row for row in rows}

    def test_default_build(self):
        install = FakeInstall(self.root)
        pack, _ = self.build(install)

        self.assertEqual(pack.version, "PFH2")
        self.assertEqual(pack.type, s2.PACK_TYPE_MOVIE)
        self.assertEqual(sorted(e.path for e in pack.entries.values()), [
            "db/faction_banners_tables/faction_banners",
            "db/faction_uniform_colours_tables/faction_uniform_colours",
            "db/factions_tables/factions",
            "db/uniform_to_faction_colours_tables/uniform_to_faction_colours",
            "text/db/factions.loc",
        ])

        data, factions = self.table(pack, "db/factions_tables/factions", "factions_tables")
        shimazu = factions["clan_shimazu"]
        red, black = (180, 20, 20), (20, 20, 20)
        for group, colour in (("primary", red), ("secondary", black), ("alt_primary", black),
                              ("alt_secondary", red), ("uniform", red), ("alt_uniform", black)):
            got = tuple(shimazu["%s_colour_%s" % (group, c)].value for c in "rgb")
            self.assertEqual(got, tuple(float(v) for v in colour), group)
        self.assertEqual([shimazu["rebel_colour_" + c].value for c in "rgb"], [1.0, 2.0, 3.0])
        self.assertEqual(factions["clan_takeda"]["primary_colour_r"].value, 60.0)
        self.assertEqual(factions["rebel_clan_shimazu"]["primary_colour_r"].value, 70.0)

        # The patch pack's table is the base, and only colour bytes of the one row changed.
        colour_offsets = set()
        for name, cell in shimazu.items():
            if build_mod.colour_column(name) and not name.startswith("rebel"):
                colour_offsets.update(range(cell.offset, cell.offset + 4))
        diff = {i for i, (a, b) in enumerate(zip(data, install.patched_factions)) if a != b}
        self.assertEqual(len(data), len(install.patched_factions))
        self.assertTrue(diff and diff <= colour_offsets)

        _, uniforms = self.table(pack, "db/faction_uniform_colours_tables/faction_uniform_colours",
                                 "faction_uniform_colours_tables")
        self.assertEqual([uniforms["clan_shimazu"]["%s_colour_%s" % (g, c)].value
                          for g in ("primary", "secondary", "tertiary") for c in "rgb"],
                         list(red + black + black))
        self.assertEqual(uniforms["clan_takeda"]["primary_colour_r"].value, 12)

        _, banners = self.table(pack, "db/faction_banners_tables/faction_banners", "faction_banners_tables")
        self.assertEqual([banners["clan_shimazu"]["primary_" + c].value for c in ("red", "green", "blue")],
                         list(red))
        self.assertEqual(banners["clan_shimazu"]["symbol"].value, "ui/mon/shimazu")

        _, overrides = self.table(pack, "db/uniform_to_faction_colours_tables/uniform_to_faction_colours",
                                  "uniform_to_faction_colours_tables")
        self.assertEqual(overrides["clan_shimazu"]["primary_colour_r"].value, 180)
        self.assertEqual(overrides[""]["primary_colour_r"].value, 15)

        _, entries = s2.decode_loc(pack.read(pack.entries["text/db/factions.loc"]))
        self.assertEqual({e.key: e.text for e in entries}, {
            "factions_screen_name_clan_shimazu": "Toshima",
            "factions_screen_adjective_clan_shimazu": "Toshima",
            "factions_screen_name_when_shogun_clan_shimazu": "Toshima Shogunate",
            "factions_screen_name_rebel_clan_shimazu": "Shimazu Rebels",
            "factions_screen_name_clan_takeda": "Takeda",
        })

    def test_zero_to_one_float_colours(self):
        install = FakeInstall(self.root, factions_scale=1.0)
        pack, _ = self.build(install)
        _, factions = self.table(pack, "db/factions_tables/factions", "factions_tables")
        self.assertAlmostEqual(factions["clan_shimazu"]["primary_colour_r"].value, 180 / 255.0, places=6)
        self.assertAlmostEqual(factions["clan_shimazu"]["secondary_colour_r"].value, 20 / 255.0, places=6)

    def test_custom_faction_name_and_colours(self):
        install = FakeInstall(self.root)
        pack, _ = self.build(install, "--faction", "clan_takeda", "--name", "Toshima",
                             "--primary", "255,0,0", "--secondary", "#000000", "--pack-type", "mod")
        self.assertEqual((pack.version, pack.type), ("PFH3", s2.PACK_TYPE_MOD))
        _, factions = self.table(pack, "db/factions_tables/factions", "factions_tables")
        self.assertEqual(factions["clan_takeda"]["primary_colour_r"].value, 255.0)
        self.assertEqual(factions["clan_shimazu"]["primary_colour_r"].value, 50.0)
        _, entries = s2.decode_loc(pack.read(pack.entries["text/db/factions.loc"]))
        self.assertEqual({e.key: e.text for e in entries}["factions_screen_name_clan_takeda"], "Toshima")

    def test_language_from_language_txt(self):
        install = FakeInstall(self.root)
        with open(os.path.join(install.data_dir, "language.txt"), "w") as f:
            f.write("DE")
        pack, _ = self.build(install)
        _, entries = s2.decode_loc(pack.read(pack.entries["text/db/factions.loc"]))
        self.assertEqual([e.text for e in entries], ["Toshima"])

    def test_unknown_faction_lists_known_ones(self):
        install = FakeInstall(self.root)
        code, _, err = self.run_tool("--game-dir", install.data_dir, "--faction", "clan_nope")
        self.assertEqual(code, 1)
        self.assertIn("clan_takeda", err)
        self.assertFalse(os.path.exists(os.path.join(install.data_dir, "toshima_clan.pack")))

    def test_unknown_table_version_is_an_error(self):
        install = FakeInstall(self.root, factions_version=99)
        code, _, err = self.run_tool("--game-dir", install.data_dir)
        self.assertEqual(code, 1)
        self.assertIn("table layouts don't match", err)

    def test_rebuild_overwrites_own_pack_but_not_game_packs(self):
        install = FakeInstall(self.root)
        self.build(install)
        self.build(install)
        code, _, err = self.run_tool("--game-dir", install.data_dir, "--pack-name", "patch.pack")
        self.assertEqual(code, 1)
        self.assertIn("refusing to overwrite", err)

    def test_list_factions(self):
        install = FakeInstall(self.root)
        code, out, _ = self.run_tool("--game-dir", install.data_dir, "--list-factions")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"clan_shimazu\s+Shimazu\n")
        self.assertRegex(out, r"rebel_clan_shimazu\s+Shimazu Rebels\n")
        self.assertIn("3 factions", out)


if __name__ == "__main__":
    unittest.main()
