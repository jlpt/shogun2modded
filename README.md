# Shogun 2: the Toshima clan

A small mod for **Total War: Shogun 2** that turns one clan into the **Toshima** clan with **red and black**
colours. By default the Shimazu are renamed. You can choose any other clan, name or colours.

What changes in game:

| Where | What |
| --- | --- |
| Campaign map and UI | The clan's territory colour, plus its name everywhere the game shows it (for example "Toshima", "Toshima Shogunate") |
| Battles | Soldiers' armour, clothing and sashimono in red and black |
| Banners | Red and black. The clan's mon symbol isn't changed |

## Why there's a build script instead of a ready-made `.pack`

A clan's colours live in one row of the game's `factions` table, next to about 50 other settings.
A mod has to ship that whole table, so it must be built from *your* copy of the game, and game files
can't be redistributed here anyway. `build_mod.py` reads the tables from your install and changes
only the colour values and name strings. Then it writes the mod pack for you. It takes a few seconds.

## Install

1. Install **Python 3** from [python.org](https://www.python.org/downloads/). On Windows, tick
   **"Add python.exe to PATH"** in the installer.
2. Download this repository (**Code → Download ZIP**) and unzip it anywhere.
3. **Windows:** double-click `build_mod.bat`.
   **Linux / macOS:** run `python3 build_mod.py`.
4. The script finds Shogun 2 through Steam and writes `toshima_clan.pack` into the game's `data` folder.
   It's a *movie* pack, so the game loads it automatically.
5. Start Shogun 2 and begin a **new campaign**. Pick the Toshima (formerly Shimazu) clan, or play against them.

If the game isn't found, point the script at the install folder:

```
py build_mod.py --game-dir "D:\SteamLibrary\steamapps\common\Total War SHOGUN 2"
```

**To uninstall**, delete `toshima_clan.pack` from the `data` folder.

## Options

Run these from a command prompt in the unzipped folder (`py build_mod.py ...` on Windows,
`python3 build_mod.py ...` elsewhere).

| Option | Default | Meaning |
| --- | --- | --- |
| `--list-factions` | | Print every clan key with its current name, then exit |
| `--faction KEY` | `clan_shimazu` | Which clan to change (use a key from `--list-factions`) |
| `--name NAME` | `Toshima` | The clan's new name |
| `--primary COLOUR` | `#B41414` (red) | Main colour: map, banners, armour. Hex (`#C00000`) or `r,g,b` (`192,0,0`) |
| `--secondary COLOUR` | `#141414` (black) | Second colour: trim, banner detail, clothing |
| `--game-dir PATH` | found via Steam | Shogun 2 install folder |
| `--language CODE` | from `language.txt` | Which language's text to rename (`en`, `ge`, `fr`, ...) |
| `--pack-type mod` | `movie` | Build a normal mod pack instead. You then enable it by adding `mod "toshima_clan.pack";` to `%APPDATA%\The Creative Assembly\Shogun2\scripts\user.script.txt` |
| `--output PATH` | the game's `data` folder | Write the pack somewhere else |

For example, to make the Date clan the Toshima clan in pure red and black (check the exact key
with `--list-factions`):

```
py build_mod.py --faction clan_date --primary "#FF0000" --secondary "#000000"
```

Re-running the script replaces the pack it built before. It never overwrites the game's own packs.

## Good to know

- **Start a new campaign.** Existing saves may keep the old colours.
- Only the clan's name entries are renamed. Other text that mentions the old name may still say
  "Shimazu", such as the clan description on the campaign-select screen, event messages or
  character family names.
- Other mods that change clan colours or `factions` will conflict with this one. Disable them if
  the colours don't show up.
- The script only reads packs that ship with the game. It ignores other mods in the `data` folder.
- Mods can cause desyncs in multiplayer, so delete the pack before playing online.
- If the script can't read the game's `factions` table (for example after a game update changes its
  layout), it stops with an error and doesn't write a broken pack.

## Doing it by hand

If the script can't handle your game version, you can make the same edits in
[RPFM](https://github.com/Frodo45127/rpfm), the community pack editor:

1. Set RPFM's game to **Shogun 2** and create a new pack.
2. From the game files (RPFM's *Dependencies* panel), add these to your pack:
   `db/factions_tables/*`, `db/faction_uniform_colours_tables/*`, `db/faction_banners_tables/*`
   and `text/db/factions.loc` (from your language's `local_xx` pack).
3. In each table, find the clan's row (`clan_shimazu`). Set the `primary_*` colour columns to red
   (180, 20, 20) and the `secondary_*` / `tertiary_*` columns to black (20, 20, 20).
4. In `factions.loc`, change `factions_screen_name_clan_shimazu` and
   `factions_screen_adjective_clan_shimazu` to `Toshima`.
5. Change the pack type to **Movie** and save it into the game's `data` folder.

## For developers

- `build_mod.py` is the command-line tool and the mod logic.
- `s2formats.py` reads and writes packs (PFH0/PFH2/PFH3), DB tables and LOC files.
- `s2_table_schemas.json` holds the column layouts of the four colour tables, taken from RPFM's
  [Shogun 2 schema](https://github.com/Frodo45127/rpfm-schemas). Regenerate it with
  `python tools/extract_schemas.py schema_sho2.ron`.
- Tests build a fake Shogun 2 install and run the tool end to end:

  ```
  python -m unittest discover -s tests
  ```
