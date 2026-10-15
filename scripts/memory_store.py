#!/usr/bin/env python3
"""Memoria store: `load` and `save` for per-day, per-project Markdown files in ./sessions of the current project folder."""

import argparse
import copy
import json
import os
import re
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

SCHEMA_VERSION = 2
KINDS = {"preference", "fact", "decision", "proposal", "open_question"}
EVIDENCE = {"explicit_user", "observed", "inferred", "assistant_proposal"}
STATUSES = {"active", "uncertain", "superseded"}
REQUIRED = ("id", "kind", "scope", "source", "evidence", "status", "title", "description", "datetime")
FIELD_ORDER = ("id", "title", "kind", "status", "evidence", "scope", "description", "source", "valid_until", "created_at", "supersedes")
TOP_KEYS = ("schema_version", "revision", "updated_at", "scope", "coverage")
WORD_BUDGET = 1500
STORE_DIR_NAME = "sessions"
RESERVED_NAMES = {"all"}
DECISIONS_FILE = "decisions.jsonl"

SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
FILE_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]*)\.md")
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# safe_dump indents everything below a top-level key, so a column-0 fence can never occur inside dumped data
FENCE = re.compile(r"^~~~yaml\n(.*?)\n~~~$", re.S | re.M)


class StoreError(Exception):
    pass


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def plain(value):
    # PyYAML turns unquoted ISO timestamps into datetime/date objects; the store keeps them as strings.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def parse_aware(text, what):
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError:
        raise StoreError(f"{what}: {text!r} is not an ISO 8601 datetime")
    if parsed.tzinfo is None:
        raise StoreError(f"{what}: {text!r} has no timezone")


def default_store_dir():
    return Path.cwd() / STORE_DIR_NAME


def default_project():
    ascii_name = unicodedata.normalize("NFKD", Path.cwd().name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise StoreError("cannot derive a project name from the working directory; pass --project")
    return slug


def check_slug(project):
    if not SLUG.fullmatch(project or "") or project in RESERVED_NAMES:
        raise StoreError(f"project {project!r} must be a lowercase ASCII slug (a-z, 0-9, -) other than 'all', e.g. 'memorizace'")


def store_file(store_dir, date, project):
    return Path(store_dir) / f"{date}-{project}.md"


def day_files(store_dir):
    """(date, project, path) of every day file, newest day first."""
    found = []
    for path in Path(store_dir).glob("*.md"):
        match = FILE_NAME.fullmatch(path.name)
        if match:
            found.append((match.group(1), match.group(2), path))
    return sorted(found, key=lambda f: (f[0], f[1]), reverse=True)


def yaml_dump(obj):
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100)


def fence(obj):
    return "~~~yaml\n" + yaml_dump(obj) + "~~~\n"


def ordered(record):
    keys = [k for k in FIELD_ORDER if k in record]
    keys += [k for k in record if k not in FIELD_ORDER and k != "datetime"]
    keys += ["datetime"] if "datetime" in record else []
    return {k: record[k] for k in keys}


def render(path, data):
    date, project = FILE_NAME.fullmatch(path.name).groups()
    front = {k: data[k] for k in TOP_KEYS if k in data}
    parts = ["---\n" + yaml_dump(front) + "---\n", f"# {project} — {date}\n"]
    if data.get("checkpoint"):
        parts.append("## Checkpoint\n\n" + fence(data["checkpoint"]))
    parts.append("## Records\n")
    for record in data["records"]:
        title = " ".join(str(record["title"]).split())
        parts.append(f"### {title}\n\n" + fence(ordered(record)))
    return "\n".join(parts)


def parse(text, path):
    front = FRONT_MATTER.match(text)
    if not front:
        raise StoreError(f"{path} has no front matter, leaving it untouched")
    try:
        data = plain(yaml.safe_load(front.group(1)))
        checkpoint_part, _, records_part = text[front.end():].partition("\n## Records")
        records = [plain(yaml.safe_load(block)) for block in FENCE.findall(records_part)]
        checkpoint = FENCE.search(checkpoint_part)
        if checkpoint:
            checkpoint = plain(yaml.safe_load(checkpoint.group(1)))
    except yaml.YAMLError as e:
        raise StoreError(f"{path} is not valid YAML, leaving it untouched: {e}")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise StoreError(f"{path} has an unknown schema (expected schema_version {SCHEMA_VERSION}), leaving it untouched")
    if not isinstance(data.get("revision"), int):
        raise StoreError(f"{path} is malformed (revision), leaving it untouched")
    data["records"] = records
    if checkpoint:
        data["checkpoint"] = checkpoint
    return data


def read_store(path):
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "revision": 0, "records": []}
    return parse(path.read_text(encoding="utf-8"), path)


def validate_store(data):
    if data["revision"] > 0:
        parse_aware(data.get("updated_at"), "updated_at")
    seen = set()
    for record in data["records"]:
        if not isinstance(record, dict):
            raise StoreError("a record block is not a mapping")
        rid = record.get("id")
        for field in REQUIRED:
            if record.get(field) in (None, "", [], {}):
                raise StoreError(f"record {rid or record.get('title')!r}: missing or empty `{field}`")
        if rid in seen:
            raise StoreError(f"duplicate id {rid}")
        seen.add(rid)
        for field, allowed in (("kind", KINDS), ("evidence", EVIDENCE), ("status", STATUSES)):
            if record[field] not in allowed:
                raise StoreError(f"record {rid}: `{field}` = {record[field]!r}, allowed: {sorted(allowed)}")
        parse_aware(record["datetime"], f"record {rid} datetime")


def write_store(path, data):
    """Temp file in the same directory, round-trip check, atomic replace, read-back. Returns the word count."""
    text = render(path, data)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if parse(tmp.read_text(encoding="utf-8"), tmp) != plain(data):
        tmp.unlink()
        raise StoreError("temporary file does not round-trip to the intended content; store untouched")
    os.replace(tmp, path)
    if read_store(path) != plain(data):
        raise StoreError(f"read-back of {path} differs from what was written")
    return len(text.split())


def content(record):
    return {k: v for k, v in record.items() if k not in ("id", "datetime")}


def saved_at(item):
    stamp = item[3].get("updated_at")
    return datetime.fromisoformat(stamp) if stamp else datetime.min.replace(tzinfo=timezone.utc)


@contextmanager
def locked(path):
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise StoreError(f"{lock} exists: another writer is active or crashed; not removing it automatically")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def parse_decisions(text, path):
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            raise StoreError(f"{path} line {number} is not valid JSON, leaving it untouched: {e}")
        if not isinstance(entry, dict):
            raise StoreError(f"{path} line {number} is not a JSON object, leaving it untouched")
        entries.append(entry)
    return entries


def read_decisions(path):
    return parse_decisions(path.read_text(encoding="utf-8"), path) if path.exists() else []


def check_decisions(items):
    if not isinstance(items, list):
        raise StoreError("`decisions` must be a list of {decision, source} items")
    for item in items:
        if not isinstance(item, dict) or not str(item.get("decision") or "").strip() or not str(item.get("source") or "").strip():
            raise StoreError("each decision needs a non-empty `decision` and `source`")


def add_decisions(path, project, items, stamp):
    """Append new decisions to decisions.jsonl, creating it when missing; exact duplicates are skipped."""
    with locked(path):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        stored = parse_decisions(existing, path)
        seen = {(d.get("project"), d.get("decision")) for d in stored}
        fresh = []
        for item in items:
            decision = " ".join(str(item["decision"]).split())
            if (project, decision) in seen:
                continue
            seen.add((project, decision))
            fresh.append({"datetime": stamp, "project": project, "decision": decision, "source": " ".join(str(item["source"]).split())})

        if fresh or not path.exists():
            expected = stored + fresh
            text = existing + ("\n" if existing and not existing.endswith("\n") else "")
            text += "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in fresh)
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if parse_decisions(tmp.read_text(encoding="utf-8"), tmp) != expected:
                tmp.unlink()
                raise StoreError("temporary decisions file does not round-trip; decisions.jsonl untouched")
            os.replace(tmp, path)
            if read_decisions(path) != expected:
                raise StoreError(f"read-back of {path} differs from what was written")
    return {"added": len(fresh), "unchanged": len(items) - len(fresh)}


def load(store_dir=None, target=None, query=None):
    """target None: the latest file; "all": every file; otherwise a file name (without .md) or a project name."""
    store_dir = Path(store_dir) if store_dir else default_store_dir()
    today = now_utc()[:10]
    found = day_files(store_dir)

    if target == "all":
        chosen = found
    elif target is None:
        chosen = [f for f in found if f[0] == found[0][0]]
    else:
        stem = target.removesuffix(".md")
        chosen = [f for f in found if f[2].stem == stem] or next(([f] for f in found if f[1] == stem), [])
        if not chosen:
            raise StoreError(f"no file or project named {target!r} in {store_dir}; plain `load` shows the latest file, `load all` everything")

    loaded = []
    for date, project, path in chosen:
        data = read_store(path)
        validate_store(data)
        loaded.append((date, project, path, data))
    loaded.sort(key=lambda item: (saved_at(item), item[2].name))
    if target is None:
        loaded = loaded[-1:]

    today_revisions = {project: read_store(path)["revision"] for date, project, path in found if date == today}
    superseded = {r["supersedes"]: r["id"] for *_, data in loaded for r in data["records"] if "supersedes" in r}
    needle = query.casefold() if query else None

    files = []
    for date, project, path, data in loaded:
        records = []
        for record in data["records"]:
            if needle and needle not in record["title"].casefold() and needle not in record["description"].casefold():
                continue
            records.append({**record, "superseded_by": superseded[record["id"]]} if record["id"] in superseded else record)
        if needle and not records:
            continue
        files.append({
            "file": path.name,
            "date": date,
            "project": project,
            "revision": data["revision"],
            "updated_at": data.get("updated_at"),
            "scope": data.get("scope"),
            "coverage": data.get("coverage"),
            "checkpoint": data.get("checkpoint"),
            "records": records,
        })
    return {
        "store_dir": str(store_dir),
        "today": today,
        "today_revisions": today_revisions,
        "decisions": read_decisions(store_dir / DECISIONS_FILE),
        "files": files,
    }


def merge(store, proposal, stamp, elsewhere):
    """Return (new_store, report). `elsewhere` is [(file name, record)] from the project's other days.

    The new store equals the old one when nothing changed.
    """
    new = copy.deepcopy(store)
    added, updated, unchanged = [], [], []
    by_id = {r["id"]: r for r in new["records"]}
    other_by_id = {r["id"]: (name, r) for name, r in elsewhere}

    for item in proposal.get("records", []):
        item = {k: v for k, v in item.items() if k != "datetime"}
        rid = item.get("id")
        if rid in by_id:
            current = by_id[rid]
            merged = {**current, **item}
            if content(merged) == content(current):
                unchanged.append(rid)
                continue
            merged["datetime"] = stamp
            current.clear()
            current.update(merged)
            updated.append(rid)
            continue
        if rid in other_by_id:
            name, current = other_by_id[rid]
            if content({**current, **item}) == content(current):
                unchanged.append(rid)
                continue
            raise StoreError(f"record {rid} belongs to {name} (an earlier day); add a new record with `supersedes: {rid}` instead of editing it")
        twin = next((r for r in [*new["records"], *(r for _, r in elsewhere)] if content(r) == content(item)), None)
        if twin:
            unchanged.append(twin["id"])
            continue
        rid = rid or f"mem-{uuid4().hex[:8]}"
        record = {"id": rid, **{k: v for k, v in item.items() if k != "id"}, "datetime": stamp}
        new["records"].append(record)
        by_id[rid] = record
        added.append(rid)

    known = set(by_id) | set(other_by_id)
    for record in new["records"]:
        if record.get("supersedes") and record["supersedes"] not in known:
            raise StoreError(f"record {record['id']}: `supersedes` points to unknown id {record['supersedes']}")

    # scope/coverage alone never justify creating a day file; records or a checkpoint do
    top_changed = False
    for key in ("scope", "coverage", "checkpoint"):
        if key in proposal and proposal[key] != new.get(key):
            new[key] = proposal[key]
            top_changed = top_changed or store["revision"] > 0 or key == "checkpoint"

    report = {"added": added, "updated": updated, "unchanged": unchanged}
    if added or updated or top_changed:
        new["schema_version"] = SCHEMA_VERSION
        new["revision"] = store["revision"] + 1
        new["updated_at"] = stamp
    return new, report


def save(proposal, project, expected_revision, store_dir=None):
    check_slug(project)
    store_dir = Path(store_dir) if store_dir else default_store_dir()
    if not isinstance(proposal, dict) or not isinstance(proposal.get("records", []), list):
        raise StoreError("proposal must be a mapping with a `records` list")
    proposal = plain(proposal)
    stamp = now_utc()
    target = store_file(store_dir, stamp[:10], project)

    decisions = proposal.get("decisions", [])
    check_decisions(decisions)
    read_decisions(store_dir / DECISIONS_FILE)  # a corrupt log aborts the save before anything is written

    store_dir.mkdir(parents=True, exist_ok=True)
    with locked(target):
        result = _save_locked(target, store_dir, project, proposal, expected_revision, stamp)
    result["decisions"] = add_decisions(store_dir / DECISIONS_FILE, project, decisions, stamp)
    return result


def _save_locked(target, store_dir, project, proposal, expected_revision, stamp):
    store = read_store(target)
    if store["revision"] != expected_revision:
        raise StoreError(f"revision conflict: {target.name} is at {store['revision']}, expected {expected_revision}; run load again and merge")

    elsewhere = []
    for _, file_project, path in day_files(store_dir):
        if file_project == project and path != target:
            other = read_store(path)
            validate_store(other)
            elsewhere += [(path.name, r) for r in other["records"]]

    new, report = merge(store, proposal, stamp, elsewhere)
    changed = new["revision"] != store["revision"]
    if changed and store["revision"] == 0 and not (new.get("scope") and new.get("coverage")):
        raise StoreError(f"{target.name} does not exist yet: a new day file needs `scope` and `coverage` in the proposal")
    result ={"path": str(target), "changed": changed, "revision": new["revision"], **report}
    if not changed:
        return result

    validate_store(new)
    words = write_store(target, new)
    if words > WORD_BUDGET:
        result["warnings"] = [f"{target.name} is {words} words, above the ~{WORD_BUDGET} word budget; consider a narrower project name"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_load = sub.add_parser("load", help="print memory as JSON without modifying anything")
    p_load.add_argument("target", nargs="?", help="omit: the latest file; 'all': every file in saved order; otherwise a file name (2026-10-15-memorizace) or a project name (memorizace)")
    p_load.add_argument("--store-dir", help="default: ./sessions")
    p_load.add_argument("--query", help="case-insensitive literal substring over title and description")
    p_save = sub.add_parser("save", help="merge a proposal (YAML or JSON) into today's file for the project")
    p_save.add_argument("--store-dir", help="default: ./sessions")
    p_save.add_argument("--project", help="project name (lowercase slug); default: the working directory's name")
    p_save.add_argument("--input", required=True)
    p_save.add_argument("--expected-revision", type=int, required=True, help="today_revisions[project] from the latest load; 0 if absent")
    args = parser.parse_args()

    try:
        if args.command == "load":
            out = load(args.store_dir, args.target, args.query)
        else:
            proposal = yaml.safe_load(Path(args.input).read_text(encoding="utf-8"))
            out = save(proposal, args.project or default_project(), args.expected_revision, args.store_dir)
    except (StoreError, OSError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
