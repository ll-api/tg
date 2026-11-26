"""Generate the Telegram Bot API snapshots from the official documentation."""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_URL = "https://core.telegram.org/bots/api"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
VERSION_PATTERN = re.compile(r"Bot API [0-9]+\.[0-9]+")
DATE_PATTERN = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r" [0-9]{1,2}, [0-9]{4}"
)
NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*")
UNION_SEPARATOR = re.compile(r"\s+or\s+|\s*,\s*|\s+and\s+", re.IGNORECASE)


@dataclass
class Section:
    name: str
    description: str = ""
    kind: str = ""
    fields: list[dict] | None = None
    options: list[str] | None = None


class TelegramHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.current = None
        self.page_text = []
        self.heading = None
        self.paragraph = None
        self.table = None
        self.row = None
        self.cell = None
        self.list_item = None
        self.hidden = None

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            return
        if tag in {"script", "style"}:
            self.hidden = tag
        elif tag in {"h2", "h3"}:
            self.current = None
        elif tag == "h4":
            self.current = None
            self.heading = []
        elif self.current is not None:
            if tag == "p" and not self.current.description and self.table is None:
                self.paragraph = []
            elif tag == "table":
                self.table = []
            elif tag == "li":
                self.list_item = []
            elif self.table is not None:
                if tag == "tr":
                    self.row = []
                elif tag in {"td", "th"} and self.row is not None:
                    self.cell = []

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden:
                self.hidden = None
            return
        if tag == "h4" and self.heading is not None:
            name = clean(self.heading)
            if NAME_PATTERN.fullmatch(name):
                self.current = Section(name)
                self.sections.append(self.current)
            self.heading = None
        elif tag == "p" and self.paragraph is not None:
            if self.current is not None:
                self.current.description = clean(self.paragraph)
            self.paragraph = None
        elif tag == "li" and self.list_item is not None:
            if self.current is not None:
                if self.current.options is None:
                    self.current.options = []
                self.current.options.append(clean(self.list_item))
            self.list_item = None
        elif tag in {"td", "th"} and self.cell is not None:
            self.row.append(clean(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            kind, fields = parse_table(self.table)
            if kind and self.current is not None:
                self.current.kind, self.current.fields = kind, fields
            self.table = self.row = self.cell = None

    def handle_data(self, text):
        if self.hidden:
            return
        self.page_text.append(text)
        for capture in (self.heading, self.paragraph, self.cell, self.list_item):
            if capture is not None:
                capture.append(text)


def clean(parts):
    return " ".join(" ".join(parts).split())


def parse_types(value):
    """Parse Telegram's prose union notation while preserving array nesting."""
    value = clean([value])
    if not value:
        return []
    prefix = ""
    inner = value
    if value.startswith("Array of "):
        prefix, inner = "Array of ", value[len("Array of "):]
    parts = [part.strip() for part in UNION_SEPARATOR.split(inner) if part.strip()]
    return [prefix + part for part in parts] or [value]


def mentioned_types(text, known):
    """Return known type names in documentation order, without guessing new types."""
    matches = []
    for name in sorted(known, key=len, reverse=True):
        for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
            matches.append((match.start(), name))
    result = []
    for _, name in sorted(matches):
        if name not in result:
            result.append(name)
    return result


def extract_returns(description, known):
    """Extract only explicit return mentions from method prose."""
    candidates = []
    for match in re.finditer(r"\b(?:on success\s*,\s*|returns?\s+|returned\s+|is returned\s+)([^.]*)", description, re.IGNORECASE):
        text = match.group(1)
        found = []
        for name in sorted(known, key=len, reverse=True):
            for type_match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
                value = name
                prefix = text[:type_match.start()].rstrip()
                if re.search(r"array\s+of\s*$", prefix, re.IGNORECASE):
                    value = "Array of " + value
                found.append((type_match.start(), value))
        for _, value in sorted(found):
            if value not in candidates:
                candidates.append(value)
    return candidates


def attach_derived_metadata(methods, types, sections):
    known = set(types) | {"String", "Integer", "Boolean", "True", "Float"}
    section_by_name = {section.name: section for section in sections}
    for name, method in methods.items():
        method["returns"] = extract_returns(" ".join(method["description"]), known)
    for name, typ in types.items():
        section = section_by_name[name]
        options = section.options or []
        subtypes = [option for option in options if option in types and option != name]
        if subtypes:
            typ["subtypes"] = subtypes
    for name, typ in types.items():
        for subtype in typ.get("subtypes", []):
            types[subtype].setdefault("subtype_of", []).append(name)


def parse_table(rows):
    if not rows or rows[0][0] not in {"Field", "Parameter"}:
        return "", None
    columns = {name: index for index, name in enumerate(rows[0])}
    if not {"Type", "Description"} <= columns.keys():
        raise ValueError("API table lacks a Type or Description column")
    type_index = columns["Type"]
    description_index = columns["Description"]
    required_index = columns.get("Required")
    fields = []
    for row in rows[1:]:
        if len(row) < len(rows[0]) or not row[0] or not row[type_index]:
            raise ValueError(f"invalid row in API table: {row!r}")
        description = row[description_index]
        required = (
            row[required_index].lower() == "yes"
            if required_index is not None
            else not description.lower().startswith("optional")
        )
        fields.append({
            "name": row[0],
            "types": parse_types(row[type_index]),
            "required": required,
            "description": description,
        })
    return rows[0][0], fields


def parse(payload):
    parser = TelegramHTMLParser()
    parser.feed(payload.decode("utf-8"))
    parser.close()
    page = clean(parser.page_text)
    version = VERSION_PATTERN.search(page)
    if version is None:
        raise ValueError("Bot API version not found")
    date = DATE_PATTERN.search(page)
    methods, types = {}, {}
    for section in parser.sections:
        is_method = section.kind == "Parameter" or "requires no parameters" in section.description.lower()
        target = methods if is_method else types
        target[section.name] = {
            "name": section.name,
            "href": f"{SOURCE_URL}#{section.name.lower()}",
            "description": [section.description] if section.description else [],
            "fields": section.fields,
        }
    attach_derived_metadata(methods, types, parser.sections)
    document = {
        "version": version.group(),
        "release_date": date.group() if date else "unknown",
        "changelog": f"{SOURCE_URL}#recent-changes",
        "source": SOURCE_URL,
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "aliases": {"methods": {}, "types": {}},
        "methods": dict(sorted(methods.items())),
        "types": dict(sorted(types.items())),
    }
    validate(document)
    return document


def count_entries(document):
    methods, types = document["methods"], document["types"]
    return {
        "methods": len(methods),
        "parameters": sum(len(item["fields"] or []) for item in methods.values()),
        "types": len(types),
        "fields": sum(len(item["fields"] or []) for item in types.values()),
    }


def entity_signature(entity):
    """Return the compatibility-relevant shape, excluding prose and names."""
    return [
        {
            "name": field["name"],
            "types": field["types"],
            "required": field["required"],
        }
        for field in (entity.get("fields") or [])
    ]


def detect_renames(previous, current):
    """Find unique removed/added entities with identical field signatures."""
    aliases = {"methods": {}, "types": {}}
    for group in ("methods", "types"):
        old_names = set(previous.get(group, {}))
        new_names = set(current.get(group, {}))
        removed = old_names - new_names
        added = new_names - old_names
        for old_name in sorted(removed):
            matches = [
                new_name
                for new_name in sorted(added)
                if entity_signature(previous[group][old_name])
                == entity_signature(current[group][new_name])
            ]
            if len(matches) == 1:
                aliases[group][old_name] = matches[0]
    return aliases


def merge_aliases(previous, current):
    """Carry aliases forward and point old aliases at the newest canonical name."""
    aliases = {"methods": {}, "types": {}}
    detected = detect_renames(previous, current)
    for group in aliases:
        old_aliases = previous.get("aliases", {}).get(group, {})
        aliases[group].update(old_aliases)
        aliases[group].update(detected[group])
        for alias, target in list(aliases[group].items()):
            seen = set()
            while target in aliases[group] and target not in seen:
                seen.add(target)
                target = aliases[group][target]
            aliases[group][alias] = target
        aliases[group] = {
            alias: target
            for alias, target in aliases[group].items()
            if alias not in current.get(group, {}) and target in current.get(group, {})
        }
    current["aliases"] = aliases
    return current


def validate(document):
    derived = count_entries(document)
    reported = document.get("counts")
    if reported is not None and reported != derived:
        raise ValueError("reported counts do not match the schema")
    if not document.get("methods"):
        raise ValueError("missing methods")
    if not document.get("types"):
        raise ValueError("missing types")
    known_types = set(document["types"]) | {"String", "Integer", "Boolean", "True", "Float"}
    for method_name, method in document["methods"].items():
        for return_type in method.get("returns", []):
            base = return_type.removeprefix("Array of ")
            if base not in known_types:
                raise ValueError(f"method {method_name} returns unknown type {return_type}")
    for type_name, typ in document["types"].items():
        for subtype in typ.get("subtypes", []):
            if subtype not in document["types"]:
                raise ValueError(f"type {type_name} has unknown subtype {subtype}")
        for parent in typ.get("subtype_of", []):
            if parent not in document["types"]:
                raise ValueError(f"type {type_name} has unknown parent {parent}")
    aliases = document.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("aliases must be an object")
    for group in ("methods", "types"):
        group_aliases = aliases.get(group, {})
        if not isinstance(group_aliases, dict):
            raise ValueError(f"aliases.{group} must be an object")
        for old_name, canonical_name in group_aliases.items():
            if old_name in document[group]:
                raise ValueError(f"alias {group}.{old_name} collides with a canonical name")
            if canonical_name not in document[group]:
                raise ValueError(f"alias {group}.{old_name} points to missing {canonical_name}")


def read_source(source_html):
    request = Request(SOURCE_URL, headers={"User-Agent": "ll-api-tg-api-sync/1.0 (+https://github.com/ll-api/tg)"})
    with source_html.open("rb") if source_html else urlopen(request, timeout=45) as source:
        payload = source.read(MAX_SOURCE_BYTES + 1)
    if len(payload) > MAX_SOURCE_BYTES:
        raise ValueError(f"source exceeds {MAX_SOURCE_BYTES} bytes")
    return payload


def write_artifacts(document, directory):
    for filename, options in (("api.json", {"indent": 2}), ("api.min.json", {"separators": (",", ":")})):
        text = json.dumps(document, ensure_ascii=False, **options)
        # Keep Go's JSON escaping for HTML-sensitive characters to minimize snapshot churn.
        for char in ("&", "<", ">", "\u2028", "\u2029"):
            text = text.replace(char, f"\\u{ord(char):04x}")
        (directory / filename).write_text(text + "\n", encoding="utf-8", newline="\n")


def check_artifacts(directory):
    documents = []
    for filename in ("api.json", "api.min.json"):
        try:
            document = json.loads((directory / filename).read_text(encoding="utf-8"))
            validate(document)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"{filename}: {error}") from error
        documents.append(document)
    if documents[0] != documents[1]:
        raise ValueError("api.json and api.min.json differ")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate both JSON files without fetching HTML")
    mode.add_argument("--source-html", type=Path, help="regenerate from a local Telegram documentation snapshot")
    args = cli.parse_args()
    directory = Path(__file__).resolve().parent
    try:
        if args.check:
            check_artifacts(directory)
        else:
            document = parse(read_source(args.source_html))
            previous = None
            try:
                previous = json.loads((directory / "api.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if previous is not None:
                document = merge_aliases(previous, document)
            write_artifacts(document, directory)
            print(f"Generated both snapshots for {document['version']}: {count_entries(document)}")
    except (OSError, ValueError) as error:
        print(f"update-api: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
