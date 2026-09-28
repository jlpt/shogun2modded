#!/usr/bin/env python3
"""Regenerate s2_table_schemas.json from RPFM's Shogun 2 schema.

The mod builder only needs the column layout of a handful of tables, so instead
of shipping RPFM's 6 MB schema we keep the definitions of those tables in a
small JSON file. Run this when RPFM's schema is updated:

    curl -O https://raw.githubusercontent.com/Frodo45127/rpfm-schemas/master/schema_sho2.ron
    python tools/extract_schemas.py schema_sho2.ron
"""
import json
import os
import re
import sys

TABLES = [
    "factions_tables",
    "faction_uniform_colours_tables",
    "faction_banners_tables",
    "uniform_to_faction_colours_tables",
]

SOURCE = "https://github.com/Frodo45127/rpfm-schemas/blob/master/schema_sho2.ron"
OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s2_table_schemas.json")


def matching_bracket(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced brackets")


def table_definitions(text, table):
    start = text.find('"%s": [' % table)
    if start < 0:
        return []
    open_at = text.index("[", start)
    block = text[open_at:matching_bracket(text, open_at) + 1]

    definitions = []
    for version_match in re.finditer(r"\(\s*version: (-?\d+),\s*fields: \[", block):
        fields_open = block.index("[", version_match.start())
        fields_block = block[fields_open:matching_bracket(block, fields_open) + 1]
        fields = re.findall(r'name: "([^"]*)",\s*field_type: ([A-Za-z0-9_]+)', fields_block)
        definitions.append({
            "version": int(version_match.group(1)),
            "fields": [[name, field_type] for name, field_type in fields],
        })
    return definitions


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        text = f.read()

    tables = {}
    for table in TABLES:
        definitions = table_definitions(text, table)
        if not definitions:
            sys.exit("table %s not found in %s" % (table, sys.argv[1]))
        tables[table] = definitions

    # Hand-rolled layout so each column sits on its own line and diffs stay readable.
    lines = ["{", '  "source": %s,' % json.dumps(SOURCE), '  "tables": {']
    for t, table in enumerate(TABLES):
        lines.append("    %s: [" % json.dumps(table))
        for d, definition in enumerate(tables[table]):
            lines.append('      {"version": %d, "fields": [' % definition["version"])
            fields = ["        %s" % json.dumps(field) for field in definition["fields"]]
            lines.append(",\n".join(fields))
            lines.append("      ]}" + ("," if d < len(tables[table]) - 1 else ""))
        lines.append("    ]" + ("," if t < len(TABLES) - 1 else ""))
    lines += ["  }", "}", ""]

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s" % OUT_PATH)


if __name__ == "__main__":
    main()
