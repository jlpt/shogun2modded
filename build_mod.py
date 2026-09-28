#!/usr/bin/env python3
"""Build a Total War: Shogun 2 mod that renames one clan and recolours it.

By default the Shimazu become the Toshima clan, in red and black.

The mod is built from the tables inside your own game install, so it always
matches your game version and nothing from the game is redistributed here.

    python build_mod.py                    # find the game, write toshima_clan.pack into its data folder
    python build_mod.py --list-factions    # list clan keys you can pass to --faction
    python build_mod.py --faction clan_date --primary "#C00000" --secondary "#101010"
"""
import argparse
import glob
import json
import os
import re
import struct
import sys

import s2formats as s2

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "s2_table_schemas.json")

DEFAULT_FACTION = "clan_shimazu"
DEFAULT_NAME = "Toshima"
DEFAULT_PRIMARY = "#B41414"    # red
DEFAULT_SECONDARY = "#141414"  # black, lifted just off #000000 so armour keeps some shading
DEFAULT_PACK_NAME = "toshima_clan.pack"

# Tables holding faction colours, and whether the mod is useless without them.
COLOUR_TABLES = [
    ("factions_tables", True),                     # campaign map, UI and general faction colours
    ("faction_uniform_colours_tables", False),     # soldiers' armour, clothing and sashimono
    ("faction_banners_tables", False),             # banners
    ("uniform_to_faction_colours_tables", False),  # per-uniform overrides for a faction
]
FACTION_KEY_COLUMNS = ("faction_name", "key")

# Which of the two chosen colours each colour column group gets. Groups not listed
# here (rebel_colour) are left alone. The alt_* colours are used when both sides of
# a battle look alike, so they get the pair swapped.
COLOUR_ROLES = {
    "primary": "primary",
    "secondary": "secondary",
    "tertiary": "secondary",
    "alt_primary": "secondary",
    "alt_secondary": "primary",
    "uniform": "primary",
    "alt_uniform": "secondary",
    "card": "primary",
}
COLOUR_COLUMN_PATTERNS = [
    re.compile(r"^(?P<group>[a-z_]+)_colour_(?P<channel>[rgb])$"),              # primary_colour_r
    re.compile(r"^(?P<group>primary|secondary|tertiary)_(?P<channel>red|green|blue)$"),  # primary_red
]
CHANNELS = {"r": 0, "g": 1, "b": 2, "red": 0, "green": 1, "blue": 2}

# language.txt code -> suffix of the game's local_<suffix>.pack
LANGUAGE_PACKS = {
    "BR": "br", "CN": "cn", "CZ": "cz", "DE": "ge", "EN": "en", "ES": "sp", "FR": "fr",
    "IT": "it", "KR": "kr", "PO": "pl", "RU": "ru", "TR": "tr", "ZH": "zh",
}

GAME_FOLDER = "Total War SHOGUN 2"


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# Finding the game
# ---------------------------------------------------------------------------

def steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(winreg.QueryValueEx(key, "SteamPath")[0])
        except OSError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(os.path.join(os.environ[env], "Steam"))
    else:
        home = os.path.expanduser("~")
        roots += [
            os.path.join(home, ".steam", "steam"),
            os.path.join(home, ".local", "share", "Steam"),
            os.path.join(home, "Library", "Application Support", "Steam"),
        ]
    return roots


def steam_libraries():
    libraries = []
    for root in steam_roots():
        libraries.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for path in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                    libraries.append(path.replace("\\\\", "\\"))
        except OSError:
            pass
    unique = []
    for library in libraries:
        normalised = os.path.normcase(os.path.normpath(library))
        if normalised not in [os.path.normcase(os.path.normpath(u)) for u in unique]:
            unique.append(library)
    return unique


def find_data_dir(game_dir):
    """Return the folder holding the game's .pack files."""
    subfolders = ("", "data", os.path.join("share", "data", "data"))
    if game_dir:
        for sub in subfolders:
            candidate = os.path.join(game_dir, sub)
            if glob.glob(os.path.join(candidate, "*.pack")):
                return candidate
        raise BuildError("no .pack files found in %s or its data folder" % game_dir)

    for library in steam_libraries():
        install = os.path.join(library, "steamapps", "common", GAME_FOLDER)
        for sub in subfolders:
            candidate = os.path.join(install, sub)
            if os.path.isfile(os.path.join(candidate, "data.pack")):
                return candidate
    raise BuildError("couldn't find Shogun 2. Pass its install folder with --game-dir, for example:\n"
                     '  python build_mod.py --game-dir "D:\\SteamLibrary\\steamapps\\common\\Total War SHOGUN 2"')


def detect_language(data_dir):
    try:
        with open(os.path.join(data_dir, "language.txt"), encoding="utf-8", errors="replace") as f:
            code = f.read().strip().upper()
    except OSError:
        return "en"
    return LANGUAGE_PACKS.get(code, "en")


# ---------------------------------------------------------------------------
# Reading the game's files
# ---------------------------------------------------------------------------

class GameFiles(object):
    """The game's own files, resolved the way the game layers its packs."""

    def __init__(self, data_dir, language, log):
        pack_paths = sorted(glob.glob(os.path.join(data_dir, "*.pack")) +
                            glob.glob(os.path.join(data_dir, "localization", "*.pack")))
        self.packs = []
        for path in pack_paths:
            try:
                pack = s2.Pack(path)
            except (s2.FormatError, OSError, struct.error, ValueError) as e:
                log("  skipping %s: %s" % (os.path.basename(path), e))
                continue
            # Mod and movie packs are other people's mods (or an earlier build of this one).
            if pack.type in (s2.PACK_TYPE_MOD, s2.PACK_TYPE_MOVIE):
                continue
            local = re.match(r"local_([a-z]+)", pack.name.lower())
            if local and local.group(1) != language:
                continue
            self.packs.append(pack)
        if not self.packs:
            raise BuildError("no readable game packs in %s" % data_dir)

        # Later packs override earlier ones: higher pack type wins, and within a type the
        # alphabetically first pack wins. This is the order RPFM uses.
        ordered = sorted(self.packs, key=lambda p: p.name.lower(), reverse=True)
        ordered.sort(key=lambda p: p.type)
        self.files = {}
        for pack in ordered:
            for key, entry in pack.entries.items():
                self.files[key] = (pack, entry)

    def paths_under(self, folder):
        prefix = folder.lower().rstrip("/") + "/"
        return sorted(key for key in self.files if key.startswith(prefix))

    def paths_with_extension(self, extension):
        return sorted(key for key in self.files if key.endswith(extension))

    def read(self, key):
        pack, entry = self.files[key]
        return entry.path, pack, pack.read(entry)

    def packs_containing(self, key):
        return [pack.name for pack in self.packs if key in pack.entries]


def load_schemas():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)["tables"]


def faction_key_column(row):
    for column in FACTION_KEY_COLUMNS:
        if column in row:
            return column
    return None


def colour_column(name):
    for pattern in COLOUR_COLUMN_PATTERNS:
        match = pattern.match(name)
        if match:
            return match.group("group"), CHANNELS[match.group("channel")]
    return None


# ---------------------------------------------------------------------------
# Building the mod
# ---------------------------------------------------------------------------

def parse_colour(text):
    text = text.strip()
    match = re.match(r"^#?([0-9a-fA-F]{6})$", text)
    if match:
        value = match.group(1)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 3 and all(p.isdigit() and int(p) <= 255 for p in parts):
        return tuple(int(p) for p in parts)
    raise argparse.ArgumentTypeError('colour must look like "#B41414" or "180,20,20"')


def recolour_table(data, definitions, faction, colours):
    """Set the faction's colour cells, editing only those bytes.

    Returns (new data or None if the faction isn't in this table, changes, faction keys in the table).
    """
    _, _, rows = s2.decode_db(data, definitions)
    keys = [row[faction_key_column(row)].value for row in rows if faction_key_column(row)]

    # Float colours are stored either 0-255 or 0-1; the rest of the table tells which.
    float_values = [cell.value for row in rows for name, cell in row.items()
                    if cell.field_type == "F32" and colour_column(name)]
    float_scale = 255.0 if float_values and max(float_values) > 1.0 else 1.0

    buf = bytearray(data)
    changes = []
    for row in rows:
        key_column = faction_key_column(row)
        if key_column is None or row[key_column].value != faction:
            continue
        for name, cell in row.items():
            parsed = colour_column(name)
            if parsed is None or parsed[0] not in COLOUR_ROLES:
                continue
            value = colours[COLOUR_ROLES[parsed[0]]][parsed[1]]
            if cell.field_type == "I32":
                new = value
                struct.pack_into("<i", buf, cell.offset, new)
            elif cell.field_type == "F32":
                new = value * float_scale / 255.0
                struct.pack_into("<f", buf, cell.offset, new)
            else:
                continue
            changes.append((name, cell.value, new))
    if not changes:
        return None, [], keys
    return bytes(buf), changes, keys


def mentions(data, text):
    return text.encode("utf-8") in data or text.encode("utf-16-le") in data


def owned_loc_entries(entries, faction, all_factions):
    """Entries keyed factions_<column>_<faction>.

    When another faction key ends with this one (rebel_clan_x vs clan_x), its
    entries also end with "_clan_x", so those are left to the longer key.
    """
    suffix = "_" + faction
    longer = ["_" + f for f in all_factions if f != faction and f.endswith(suffix)]
    return [e for e in entries
            if e.key.startswith("factions_") and e.key.endswith(suffix)
            and not any(e.key.endswith(l) for l in longer)]


def build(args, log):
    schemas = load_schemas()
    data_dir = find_data_dir(args.game_dir)
    language = args.language or detect_language(data_dir)
    log("Game data: %s (language: %s)" % (data_dir, language))
    game = GameFiles(data_dir, language, log)

    out_files = {}
    colours = {"primary": args.primary, "secondary": args.secondary}

    # --- Colour tables. The faction must at least exist in factions_tables. ---
    all_factions = []
    faction_found = False
    for table, required in COLOUR_TABLES:
        definitions = schemas[table]
        for key in game.paths_under("db/" + table):
            path, pack, data = game.read(key)
            if not mentions(data, args.faction) and table != "factions_tables":
                continue
            try:
                new_data, changes, keys = recolour_table(data, definitions, args.faction, colours)
            except (s2.FormatError, struct.error) as e:
                message = "couldn't read %s from %s (%s)" % (path, pack.name, e)
                if required and mentions(data, args.faction):
                    raise BuildError(message + ". The bundled table layouts don't match this game version; "
                                     "see 'Doing it by hand' in the README.")
                log("  warning: " + message)
                continue
            if table == "factions_tables":
                all_factions += keys
            if new_data is None:
                continue
            if table == "factions_tables":
                faction_found = True
            out_files[path] = new_data
            log("Recoloured %s (from %s):" % (path, pack.name))
            for name, old, new in changes:
                log("    %-24s %8.4g -> %g" % (name, old, new))
            others = [n for n in game.packs_containing(key) if n != pack.name]
            if others:
                log("    note: also present in %s; using the copy from %s" % (", ".join(others), pack.name))

    if not faction_found:
        raise BuildError("faction '%s' not found. Known factions:\n  %s"
                         % (args.faction, "\n  ".join(sorted(set(all_factions))) or "(none)"))

    # --- Localisation: the clan's display names. ---
    loc_files = []
    for key in game.paths_with_extension(".loc"):
        path, pack, data = game.read(key)
        if not mentions(data, "_" + args.faction):
            continue
        try:
            version, entries = s2.decode_loc(data)
        except (s2.FormatError, struct.error) as e:
            log("  warning: couldn't read %s from %s (%s)" % (path, pack.name, e))
            continue
        if s2.encode_loc(version, entries) != data:
            log("  warning: %s from %s doesn't re-encode byte for byte; leaving it alone" % (path, pack.name))
            continue
        owned = owned_loc_entries(entries, args.faction, all_factions)
        if owned:
            loc_files.append((path, pack, version, entries, owned))

    old_names = []
    for column in ("screen_name", "screen_adjective"):
        for _, _, _, _, owned in loc_files:
            for entry in owned:
                if entry.key == "factions_%s_%s" % (column, args.faction) and entry.text.strip():
                    if entry.text not in old_names:
                        old_names.append(entry.text)
    if not old_names:
        raise BuildError("no screen name found for %s in the %s text files, so it can't be renamed"
                         % (args.faction, language))

    # Swap every old name (and its all-caps form) in one pass, longest first.
    replacements = {}
    for old in old_names:
        replacements[old] = args.name
        replacements[old.upper()] = args.name.upper()
    pattern = re.compile("|".join(re.escape(o) for o in sorted(replacements, key=len, reverse=True)))

    for path, pack, version, entries, owned in loc_files:
        changes = []
        for entry in owned:
            new_text = pattern.sub(lambda m: replacements[m.group(0)], entry.text)
            if new_text != entry.text:
                changes.append((entry.key, entry.text, new_text))
                entry.text = new_text
        if changes:
            out_files[path] = s2.encode_loc(version, entries)
            log("Renamed in %s (from %s):" % (path, pack.name))
            for key, old, new in changes:
                log("    %s: %r -> %r" % (key, old, new))

    # --- Write the pack. ---
    output = args.output or os.path.join(data_dir, args.pack_name)
    if os.path.exists(output):
        try:
            existing = s2.Pack(output)
        except (s2.FormatError, struct.error, ValueError):
            existing = None
        if existing is None or existing.type not in (s2.PACK_TYPE_MOD, s2.PACK_TYPE_MOVIE):
            raise BuildError("%s already exists and isn't a mod pack; refusing to overwrite it" % output)

    pack_type = s2.PACK_TYPE_MOVIE if args.pack_type == "movie" else s2.PACK_TYPE_MOD
    with open(output, "wb") as f:
        f.write(s2.encode_pack(out_files, pack_type))

    log("")
    log("Wrote %s (%d files, %s pack)." % (output, len(out_files), args.pack_type))
    log("%s is now the %s clan." % (old_names[0], args.name))
    if args.pack_type == "movie":
        log("Movie packs load automatically: start Shogun 2 and begin a new campaign.")
    else:
        log('Enable it by adding this line to %APPDATA%\\The Creative Assembly\\Shogun2\\scripts\\user.script.txt:')
        log('    mod "%s";' % os.path.basename(output))
    log("To undo, delete %s." % output)
    return output


def list_factions(args, log):
    schemas = load_schemas()
    data_dir = find_data_dir(args.game_dir)
    language = args.language or detect_language(data_dir)
    game = GameFiles(data_dir, language, log)

    names = {}
    for key in game.paths_with_extension(".loc"):
        _, _, data = game.read(key)
        if not mentions(data, "factions_screen_name_"):
            continue
        try:
            _, entries = s2.decode_loc(data)
        except (s2.FormatError, struct.error):
            continue
        for entry in entries:
            if entry.key.startswith("factions_screen_name_"):
                names.setdefault(entry.key[len("factions_screen_name_"):], entry.text)

    rows = {}
    for key in game.paths_under("db/factions_tables"):
        path, pack, data = game.read(key)
        try:
            _, _, decoded = s2.decode_db(data, schemas["factions_tables"])
        except (s2.FormatError, struct.error) as e:
            log("warning: couldn't read %s from %s (%s)" % (path, pack.name, e))
            continue
        for row in decoded:
            rows[row["key"].value] = row

    for faction in sorted(rows):
        log("%-32s %s" % (faction, names.get(faction, "")))
    log("\n%d factions. Pass one to --faction." % len(rows))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rename and recolour a Shogun 2 clan.")
    parser.add_argument("--game-dir", help="Shogun 2 install folder (found automatically on Steam)")
    parser.add_argument("--faction", default=DEFAULT_FACTION, help="faction key to change (default: %(default)s)")
    parser.add_argument("--name", default=DEFAULT_NAME, help="new clan name (default: %(default)s)")
    parser.add_argument("--primary", type=parse_colour, default=parse_colour(DEFAULT_PRIMARY),
                        help="main colour, hex or r,g,b (default: %s, red)" % DEFAULT_PRIMARY)
    parser.add_argument("--secondary", type=parse_colour, default=parse_colour(DEFAULT_SECONDARY),
                        help="second colour, hex or r,g,b (default: %s, black)" % DEFAULT_SECONDARY)
    parser.add_argument("--language", help="text pack language, e.g. en or ge (default: from language.txt)")
    parser.add_argument("--pack-name", default=DEFAULT_PACK_NAME, help="output file name (default: %(default)s)")
    parser.add_argument("--pack-type", choices=("movie", "mod"), default="movie",
                        help="movie packs load automatically; mod packs need user.script.txt (default: %(default)s)")
    parser.add_argument("--output", help="write the pack here instead of the game's data folder")
    parser.add_argument("--list-factions", action="store_true", help="list faction keys and exit")
    args = parser.parse_args(argv)

    # Clan names in Russian, Chinese, etc. must not crash a console that can't show them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    def log(message):
        print(message)

    try:
        if args.list_factions:
            list_factions(args, log)
        else:
            build(args, log)
    except BuildError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
