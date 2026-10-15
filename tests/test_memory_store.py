"""Unit tests for scripts/memory_store.py.

Run from the skill directory:  python3 -m unittest discover -s tests -v
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.dont_write_bytecode = True
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_DIR / "scripts" / "memory_store.py"
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import memory_store as m  # noqa: E402

STAMP = "2026-10-15T10:00:00+00:00"
NEW = {"scope": "s", "coverage": "c"}
HOSTILE = "řádek 1: dvojtečka\n```python\nprint(1)\n```\n~~~yaml\nx: 1\n~~~\n## Records\n---\n- položka # komentář\n'uvozovky' \"dvojité\" {json: [1,2]}"


def rec(title="T", description="popis", **kw):
    return {"kind": "fact", "scope": "t", "source": "test", "evidence": "observed", "status": "active", "title": title, "description": description, **kw}


def stored_rec(rid, title="T", description="popis", dt=STAMP, **kw):
    return {"id": rid, **rec(title, description, **kw), "datetime": dt}


class StoreTest(unittest.TestCase):
    """Temp project folder, a controllable UTC clock and small helpers."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / "sessions"
        self.stamp = STAMP
        patcher = mock.patch.object(m, "now_utc", side_effect=lambda: self.stamp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, project="proj", revision=0, **proposal):
        proposal.setdefault("records", [])
        return m.save(proposal, project, revision, self.dir)

    def load(self, target=None, query=None):
        return m.load(self.dir, target, query)

    def craft(self, name, updated, records=(), checkpoint=None, revision=1):
        data = {"schema_version": m.SCHEMA_VERSION, "revision": revision, "updated_at": updated, "scope": "s", "coverage": "c", "records": list(records)}
        if checkpoint:
            data["checkpoint"] = checkpoint
        self.dir.mkdir(exist_ok=True)
        m.write_store(self.dir / name, data)

    def names(self, out):
        return [f["file"] for f in out["files"]]

    def listing(self):
        return sorted(p.name for p in self.dir.glob("*")) if self.dir.exists() else []

    def decisions_text(self):
        return (self.dir / "decisions.jsonl").read_text(encoding="utf-8")


class NamesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(os.chdir, os.getcwd())

    def test_valid_project_names(self):
        for name in ("memorizace", "a", "a-b", "2026", "a1-b2"):
            with self.subTest(name=name):
                m.check_slug(name)

    def test_invalid_project_names(self):
        for name in ("all", "Memorizace", "mem orizace", "-x", "", "česky", "a_b", None):
            with self.subTest(name=name):
                with self.assertRaises(m.StoreError):
                    m.check_slug(name)

    def test_default_project_is_the_ascii_slug_of_the_working_directory(self):
        folder = self.root / "Můj  Projekt (2026)"
        folder.mkdir()
        os.chdir(folder)
        self.assertEqual(m.default_project(), "muj-projekt-2026")

    def test_default_project_refuses_a_name_without_ascii_letters(self):
        folder = self.root / "ěščř"
        folder.mkdir()
        os.chdir(folder)
        self.assertEqual(m.default_project(), "escr")
        folder = self.root / "!!!"
        folder.mkdir()
        os.chdir(folder)
        with self.assertRaises(m.StoreError):
            m.default_project()

    def test_default_store_is_sessions_in_the_working_directory(self):
        os.chdir(self.root)
        self.assertEqual(m.default_store_dir(), Path.cwd() / "sessions")


class RenderParseTest(StoreTest):
    def test_hostile_content_survives_save_and_load(self):
        self.save(**NEW, records=[rec("Tricky ~~~ ## title", HOSTILE)], checkpoint={"goal": HOSTILE}, decisions=[{"decision": HOSTILE, "source": HOSTILE}])
        out = self.load("proj")
        self.assertEqual(out["files"][0]["records"][0]["description"], HOSTILE)
        self.assertEqual(out["files"][0]["checkpoint"]["goal"], HOSTILE)
        self.assertEqual(out["decisions"][0]["decision"], " ".join(HOSTILE.split()))

    def test_file_layout_is_readable_markdown(self):
        self.save(**NEW, records=[rec("První záznam")], checkpoint={"goal": "g"})
        text = (self.dir / "2026-10-15-proj.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nschema_version: 2\nrevision: 1\n"))
        for part in ("# proj — 2026-10-15", "## Checkpoint", "## Records", "### První záznam", "~~~yaml"):
            self.assertIn(part, text)

    def test_record_fields_are_written_in_reading_order(self):
        self.save(**NEW, records=[rec()])
        text = (self.dir / "2026-10-15-proj.md").read_text(encoding="utf-8")
        positions = [text.index(f"\n{key}: ") for key in ("id", "title", "kind", "description", "source", "datetime")]
        self.assertEqual(positions, sorted(positions))

    def test_unquoted_iso_timestamps_in_a_hand_written_file_load_as_strings(self):
        self.dir.mkdir()
        (self.dir / "2026-10-14-hand.md").write_text(
            "---\nschema_version: 2\nrevision: 1\nupdated_at: 2026-10-14T08:00:00+00:00\nscope: s\ncoverage: c\n---\n\n"
            "# hand — 2026-10-14\n\n## Records\n\n### T\n\n~~~yaml\nid: H1\ntitle: T\nkind: fact\nstatus: active\nevidence: observed\n"
            "scope: t\ndescription: d\nsource: s\ndatetime: 2026-10-14T08:00:00+00:00\n~~~\n",
            encoding="utf-8",
        )
        file = self.load()["files"][0]
        self.assertEqual(file["updated_at"], "2026-10-14T08:00:00+00:00")
        self.assertEqual(file["records"][0]["datetime"], "2026-10-14T08:00:00+00:00")

    def test_parse_refuses_broken_files(self):
        path = Path("x.md")
        for text in (
            "no front matter at all",
            "---\nschema_version: [\n---\n",
            "---\nschema_version: 99\nrevision: 1\n---\n",
            "---\nschema_version: 2\nrevision: x\n---\n",
            "---\nschema_version: 2\nrevision: 1\n---\n\n## Records\n\n~~~yaml\nid: [\n~~~\n",
        ):
            with self.subTest(text=text), self.assertRaises(m.StoreError):
                m.parse(text, path)

    def test_plain_turns_dates_into_strings(self):
        data = yaml.safe_load("a: 2026-10-14\nb: [2026-10-14T08:00:00+00:00]\nc: text")
        self.assertEqual(m.plain(data), {"a": "2026-10-14", "b": ["2026-10-14T08:00:00+00:00"], "c": "text"})


class ValidateTest(unittest.TestCase):
    def store(self, *records):
        return {"schema_version": 2, "revision": 1, "updated_at": STAMP, "records": list(records)}

    def test_accepts_a_complete_record(self):
        m.validate_store(self.store(stored_rec("R1")))

    def test_rejects_invalid_records(self):
        cases = {
            "missing field": {k: v for k, v in stored_rec("R1").items() if k != "source"},
            "empty description": stored_rec("R1", description=""),
            "bad kind": stored_rec("R1", kind="bogus"),
            "bad evidence": stored_rec("R1", evidence="rumor"),
            "bad status": stored_rec("R1", status="deleted"),
            "naive datetime": stored_rec("R1", dt="2026-10-15T10:00:00"),
            "not a datetime": stored_rec("R1", dt="yesterday"),
            "not a mapping": "text",
        }
        for label, record in cases.items():
            with self.subTest(label), self.assertRaises(m.StoreError):
                m.validate_store(self.store(record))

    def test_rejects_duplicate_ids_and_naive_updated_at(self):
        with self.assertRaises(m.StoreError):
            m.validate_store(self.store(stored_rec("R1"), stored_rec("R1", title="jiný")))
        with self.assertRaises(m.StoreError):
            m.validate_store({**self.store(), "updated_at": "2026-10-15T10:00:00"})


class SaveTest(StoreTest):
    def test_day_file_is_named_by_utc_date_and_project(self):
        out = self.save(**NEW, records=[rec()])
        self.assertTrue(out["changed"])
        self.assertEqual(out["revision"], 1)
        self.assertEqual(len(out["added"]), 1)
        self.assertEqual(self.listing(), ["2026-10-15-proj.md", "decisions.jsonl"])

    def test_a_new_utc_day_starts_a_new_file_and_leaves_the_old_one_alone(self):
        self.stamp = "2026-10-15T23:59:59+00:00"
        self.save(**NEW, records=[rec("Pondělí")])
        before = (self.dir / "2026-10-15-proj.md").read_bytes()
        self.stamp = "2026-10-16T00:00:01+00:00"
        out = self.save(**NEW, records=[rec("Úterý")])
        self.assertEqual(out["revision"], 1)
        self.assertEqual(self.listing(), ["2026-10-15-proj.md", "2026-10-16-proj.md", "decisions.jsonl"])
        self.assertEqual((self.dir / "2026-10-15-proj.md").read_bytes(), before)

    def test_new_day_file_needs_scope_and_coverage(self):
        with self.assertRaisesRegex(m.StoreError, "scope"):
            self.save(records=[rec()])
        self.assertEqual([n for n in self.listing() if n.endswith(".md")], [])
        with self.assertRaisesRegex(m.StoreError, "coverage"):
            self.save(scope="s", records=[rec()])

    def test_scope_and_coverage_alone_do_not_create_a_file(self):
        out = self.save(**NEW)
        self.assertFalse(out["changed"])
        self.assertEqual(out["revision"], 0)
        self.assertEqual([n for n in self.listing() if n.endswith(".md")], [])

    def test_a_checkpoint_alone_does_create_the_file(self):
        out = self.save(**NEW, checkpoint={"goal": "g"})
        self.assertEqual(out["revision"], 1)
        self.assertEqual(self.load("proj")["files"][0]["checkpoint"], {"goal": "g"})

    def test_saving_identical_content_changes_nothing(self):
        self.save(**NEW, records=[rec()], checkpoint={"goal": "g"})
        path = self.dir / "2026-10-15-proj.md"
        before = path.read_bytes()
        self.stamp = "2026-10-15T18:00:00+00:00"
        out = self.save(revision=1, **NEW, records=[rec()], checkpoint={"goal": "g"})
        self.assertFalse(out["changed"])
        self.assertEqual(out["revision"], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_wrong_expected_revision_is_a_conflict_that_names_the_file(self):
        self.save(**NEW, records=[rec()])
        before = (self.dir / "2026-10-15-proj.md").read_bytes()
        with self.assertRaisesRegex(m.StoreError, r"2026-10-15-proj\.md is at 1, expected 0"):
            self.save(**NEW, records=[rec("jiný")])
        self.assertEqual((self.dir / "2026-10-15-proj.md").read_bytes(), before)

    def test_update_by_id_bumps_only_the_changed_record(self):
        first, second = self.save(**NEW, records=[rec("A"), rec("B")])["added"]
        self.stamp = "2026-10-15T18:00:00+00:00"
        out = self.save(revision=1, records=[{"id": second, "status": "superseded"}])
        self.assertEqual((out["updated"], out["revision"]), ([second], 2))
        file = self.load("proj")["files"][0]
        by_id = {r["id"]: r for r in file["records"]}
        self.assertEqual(by_id[first]["datetime"], STAMP)
        self.assertEqual(by_id[second]["datetime"], self.stamp)
        self.assertEqual((by_id[second]["title"], by_id[second]["status"]), ("B", "superseded"))
        self.assertEqual(file["updated_at"], self.stamp)

    def test_exact_duplicate_without_id_is_not_added_again(self):
        original = self.save(**NEW, records=[rec()])["added"]
        out = self.save(revision=1, records=[rec()])
        self.assertEqual((out["added"], out["unchanged"], out["changed"]), ([], original, False))

    def test_new_records_get_an_id_unless_one_is_given(self):
        out = self.save(**NEW, records=[rec("A"), rec("B", id="MINE")])
        self.assertTrue(out["added"][0].startswith("mem-"))
        self.assertEqual(out["added"][1], "MINE")

    def test_datetime_from_the_proposal_is_ignored(self):
        self.save(**NEW, records=[rec(datetime="1999-01-01T00:00:00+00:00")])
        self.assertEqual(self.load("proj")["files"][0]["records"][0]["datetime"], STAMP)

    def test_records_of_earlier_days_cannot_be_edited_only_superseded(self):
        self.craft("2026-10-01-proj.md", "2026-10-01T10:00:00+00:00", [stored_rec("R1", "Stará", "původní", "2026-10-01T10:00:00+00:00")])
        with self.assertRaisesRegex(m.StoreError, "supersedes"):
            self.save(**NEW, records=[{"id": "R1", "description": "změněný"}])
        out = self.save(**NEW, records=[rec("Nová", "oprava", supersedes="R1")])
        self.assertEqual(out["revision"], 1)
        self.assertEqual(len(out["added"]), 1)
        self.assertIn("2026-10-01-proj.md", self.listing())

    def test_identical_earlier_day_content_is_unchanged_and_creates_no_file(self):
        self.craft("2026-10-01-proj.md", "2026-10-01T10:00:00+00:00", [stored_rec("R1", "Stará", "původní", "2026-10-01T10:00:00+00:00")])
        for record in ({"id": "R1", "description": "původní"}, rec("Stará", "původní")):
            out = self.save(**NEW, records=[record])
            self.assertEqual((out["unchanged"], out["changed"]), (["R1"], False))
        self.assertNotIn("2026-10-15-proj.md", self.listing())

    def test_other_projects_records_do_not_count_as_earlier_days(self):
        self.craft("2026-10-01-other.md", "2026-10-01T10:00:00+00:00", [stored_rec("R1", "Stará", "původní", "2026-10-01T10:00:00+00:00")])
        out = self.save(**NEW, records=[rec("Stará", "původní")])
        self.assertEqual(len(out["added"]), 1)

    def test_supersedes_must_point_to_a_known_id(self):
        with self.assertRaisesRegex(m.StoreError, "unknown id"):
            self.save(**NEW, records=[rec(supersedes="NENI")])
        self.assertEqual([n for n in self.listing() if n.endswith(".md")], [])

    def test_invalid_records_write_nothing(self):
        with self.assertRaisesRegex(m.StoreError, "kind"):
            self.save(**NEW, records=[rec(kind="bogus")])
        self.assertEqual([n for n in self.listing() if n.endswith(".md")], [])

    def test_invalid_project_names_and_proposals_are_refused(self):
        for name in ("All", "mem orizace", "-x"):
            with self.subTest(name=name), self.assertRaises(m.StoreError):
                self.save(name, **NEW, records=[rec()])
        for proposal in ("nope", None, {"records": "nope"}):
            with self.subTest(proposal=proposal), self.assertRaises(m.StoreError):
                m.save(proposal, "proj", 0, self.dir)

    def test_a_stale_lock_blocks_the_save_and_is_never_removed(self):
        self.dir.mkdir()
        lock = self.dir / "2026-10-15-proj.md.lock"
        lock.write_text("1")
        with self.assertRaisesRegex(m.StoreError, "lock"):
            self.save(**NEW, records=[rec()])
        self.assertTrue(lock.exists())
        self.assertNotIn("2026-10-15-proj.md", self.listing())
        lock.unlink()
        self.assertEqual(self.save(**NEW, records=[rec()])["revision"], 1)

    def test_no_lock_or_temp_files_are_left_behind(self):
        self.save(**NEW, records=[rec()], decisions=[{"decision": "d", "source": "s"}])
        with self.assertRaises(m.StoreError):
            self.save(**NEW, records=[rec("jiný")])
        self.assertEqual(self.listing(), ["2026-10-15-proj.md", "decisions.jsonl"])

    def test_oversized_day_file_is_saved_with_a_warning(self):
        out = self.save(**NEW, records=[rec("Dlouhý", " ".join(["slovo"] * (m.WORD_BUDGET + 100)))])
        self.assertEqual(out["revision"], 1)
        self.assertIn("word budget", out["warnings"][0])

    def test_small_day_file_has_no_warning(self):
        self.assertNotIn("warnings", self.save(**NEW, records=[rec()]))


class DecisionsTest(StoreTest):
    def test_the_log_is_created_empty_by_every_first_save(self):
        out = self.save(**NEW, records=[rec()])
        self.assertEqual(self.decisions_text(), "")
        self.assertEqual(out["decisions"], {"added": 0, "unchanged": 0})

    def test_each_line_carries_datetime_project_decision_and_source(self):
        self.save("alpha", **NEW, records=[rec()], decisions=[{"decision": "The skill is global.", "source": 'user: "import it globally"'}])
        entry = json.loads(self.decisions_text().splitlines()[0])
        self.assertEqual(list(entry), ["datetime", "project", "decision", "source"])
        self.assertEqual(entry, {"datetime": STAMP, "project": "alpha", "decision": "The skill is global.", "source": 'user: "import it globally"'})

    def test_non_ascii_is_stored_readably(self):
        self.save(**NEW, decisions=[{"decision": "řešení přes symlink", "source": "s"}])
        self.assertIn("řešení přes symlink", self.decisions_text())

    def test_exact_duplicates_are_skipped_after_whitespace_normalisation(self):
        self.save(**NEW, decisions=[{"decision": "Jedno rozhodnutí.", "source": "s"}])
        before = (self.dir / "decisions.jsonl").read_bytes()
        out = self.save(**NEW, decisions=[{"decision": "  Jedno   rozhodnutí. ", "source": "jiný zdroj"}])
        self.assertEqual(out["decisions"], {"added": 0, "unchanged": 1})
        self.assertEqual((self.dir / "decisions.jsonl").read_bytes(), before)

    def test_duplicates_within_one_proposal_are_counted_once(self):
        out = self.save(**NEW, decisions=[{"decision": "d", "source": "s"}, {"decision": "d", "source": "s"}])
        self.assertEqual(out["decisions"], {"added": 1, "unchanged": 1})

    def test_the_same_decision_in_another_project_is_a_different_entry(self):
        self.save("alpha", **NEW, decisions=[{"decision": "d", "source": "s"}])
        out = self.save("beta", **NEW, decisions=[{"decision": "d", "source": "s"}])
        self.assertEqual(out["decisions"]["added"], 1)

    def test_a_reversal_is_a_new_line_and_the_order_is_kept(self):
        self.save(**NEW, decisions=[{"decision": "ano", "source": "s"}])
        self.stamp = "2026-10-15T11:00:00+00:00"
        self.save(**NEW, decisions=[{"decision": "ne", "source": "s"}])
        self.assertEqual([d["decision"] for d in self.load()["decisions"]], ["ano", "ne"])

    def test_decisions_only_save_needs_no_scope_and_creates_no_day_file(self):
        out = self.save(decisions=[{"decision": "d", "source": "s"}])
        self.assertEqual((out["changed"], out["revision"], out["decisions"]["added"]), (False, 0, 1))
        self.assertEqual(self.listing(), ["decisions.jsonl"])

    def test_invalid_decisions_are_refused_before_anything_is_written(self):
        for bad in ("nope", [{"decision": "bez zdroje"}], [{"decision": "  ", "source": "s"}], [{"source": "s"}], ["text"], [{"decision": None, "source": "s"}]):
            with self.subTest(bad=bad), self.assertRaises(m.StoreError):
                self.save(**NEW, records=[rec()], decisions=bad)
        self.assertEqual(self.listing(), [])

    def test_a_corrupt_log_aborts_the_save_before_the_day_file_is_written(self):
        self.dir.mkdir()
        (self.dir / "decisions.jsonl").write_text('{"ok": 1}\nnot json\n')
        with self.assertRaisesRegex(m.StoreError, "line 2"):
            self.save(**NEW, records=[rec()])
        self.assertEqual(self.listing(), ["decisions.jsonl"])
        (self.dir / "decisions.jsonl").write_text("[1, 2]\n")
        with self.assertRaisesRegex(m.StoreError, "not a JSON object"):
            self.save(**NEW, records=[rec()])

    def test_hand_written_lines_are_kept_byte_for_byte(self):
        self.dir.mkdir()
        manual = '{"datetime":"2026-10-01T00:00:00+00:00","project":"p","decision":"ručně","source":"s"}'
        (self.dir / "decisions.jsonl").write_text(manual + "\n\n" + manual.replace("ručně", "bez konce řádku"), encoding="utf-8")
        self.save(**NEW, decisions=[{"decision": "nové", "source": "s"}])
        lines = self.decisions_text().split("\n")
        self.assertEqual(lines[0], manual)
        self.assertEqual(lines[1], "")
        self.assertIn("bez konce řádku", lines[2])
        self.assertEqual(json.loads(lines[3])["decision"], "nové")
        self.assertEqual(lines[4], "")

    def test_a_failed_day_save_appends_no_decisions(self):
        self.save(**NEW, records=[rec()])
        with self.assertRaises(m.StoreError):
            self.save(**NEW, records=[rec("jiný")], decisions=[{"decision": "d", "source": "s"}])
        self.assertEqual(self.decisions_text(), "")

    def test_a_stale_decisions_lock_blocks_and_a_rerun_recovers(self):
        self.dir.mkdir()
        lock = self.dir / "decisions.jsonl.lock"
        lock.write_text("1")
        with self.assertRaisesRegex(m.StoreError, "decisions.jsonl.lock"):
            self.save(**NEW, records=[rec()], decisions=[{"decision": "d", "source": "s"}])
        self.assertTrue(lock.exists())
        lock.unlink()
        out = self.save(revision=1, **NEW, records=[rec()], decisions=[{"decision": "d", "source": "s"}])
        self.assertEqual((out["changed"], out["decisions"]["added"]), (False, 1))


class LoadTest(StoreTest):
    def test_empty_store(self):
        out = self.load()
        self.assertEqual((out["files"], out["decisions"], out["today_revisions"], out["today"]), ([], [], {}, "2026-10-15"))
        self.assertFalse(self.dir.exists())

    def test_load_never_modifies_anything(self):
        self.save(**NEW, records=[rec()], decisions=[{"decision": "d", "source": "s"}])
        snapshot = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        for target in (None, "all", "proj", "2026-10-15-proj"):
            self.load(target)
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, snapshot)

    def build_history(self):
        self.craft("2026-10-13-alpha.md", "2026-10-13T09:00:00+00:00")
        self.craft("2026-10-14-zeta.md", "2026-10-14T08:00:00+00:00")
        self.craft("2026-10-14-alpha.md", "2026-10-14T12:00:00+00:00")
        self.craft("2026-10-14-beta.md", "2026-10-14T18:00:00+00:00")

    def test_without_a_parameter_only_the_last_saved_file_is_loaded(self):
        self.build_history()
        self.assertEqual(self.names(self.load()), ["2026-10-14-beta.md"])

    def test_last_means_saved_last_not_last_by_name(self):
        self.craft("2026-10-14-zeta.md", "2026-10-14T08:00:00+00:00")
        self.craft("2026-10-14-beta.md", "2026-10-14T18:00:00+00:00")
        self.assertEqual(self.names(self.load()), ["2026-10-14-beta.md"])

    def test_a_tie_in_save_time_falls_back_to_the_file_name(self):
        self.craft("2026-10-14-a.md", "2026-10-14T08:00:00+00:00")
        self.craft("2026-10-14-b.md", "2026-10-14T08:00:00+00:00")
        self.assertEqual(self.names(self.load()), ["2026-10-14-b.md"])
        self.assertEqual(self.names(self.load("all")), ["2026-10-14-a.md", "2026-10-14-b.md"])

    def test_all_returns_every_file_in_the_order_it_was_saved(self):
        self.build_history()
        self.assertEqual(self.names(self.load("all")), ["2026-10-13-alpha.md", "2026-10-14-zeta.md", "2026-10-14-alpha.md", "2026-10-14-beta.md"])

    def test_a_project_name_selects_that_projects_newest_day(self):
        self.build_history()
        self.assertEqual(self.names(self.load("alpha")), ["2026-10-14-alpha.md"])

    def test_a_file_name_selects_exactly_that_file_with_or_without_extension(self):
        self.build_history()
        self.assertEqual(self.names(self.load("2026-10-13-alpha")), ["2026-10-13-alpha.md"])
        self.assertEqual(self.names(self.load("2026-10-14-zeta.md")), ["2026-10-14-zeta.md"])

    def test_an_unknown_name_is_an_error_not_an_empty_result(self):
        self.build_history()
        with self.assertRaisesRegex(m.StoreError, "nonexistent"):
            self.load("nonexistent")

    def test_all_on_an_empty_folder_is_empty(self):
        self.assertEqual(self.load("all")["files"], [])

    def test_files_that_do_not_follow_the_naming_scheme_are_ignored(self):
        self.craft("2026-10-14-ok.md", "2026-10-14T08:00:00+00:00")
        for name in ("notes.md", "2026-10-15-Bad Name.md", "2026-10-15-x.md.tmp", "2026-10-15-x.md.lock", "10-15-x.md"):
            (self.dir / name).write_text("not a store")
        self.assertEqual(self.names(self.load("all")), ["2026-10-14-ok.md"])

    def test_each_file_carries_scope_coverage_checkpoint_and_records(self):
        self.save(**NEW, records=[rec()], checkpoint={"goal": "g"})
        file = self.load()["files"][0]
        self.assertEqual((file["project"], file["date"], file["revision"], file["scope"], file["coverage"], file["checkpoint"]), ("proj", "2026-10-15", 1, "s", "c", {"goal": "g"}))
        self.assertEqual(len(file["records"]), 1)

    def test_today_revisions_lists_only_todays_files(self):
        self.craft("2026-10-14-old.md", "2026-10-14T08:00:00+00:00")
        self.save("alpha", **NEW, records=[rec("a")])
        self.save("beta", **NEW, records=[rec("b")])
        self.save("alpha", 1, records=[rec("a2")])
        self.assertEqual(self.load()["today_revisions"], {"alpha": 2, "beta": 1})

    def test_decisions_come_with_every_mode_and_from_every_project(self):
        self.save("alpha", **NEW, records=[rec("a")], decisions=[{"decision": "da", "source": "s"}])
        self.stamp = "2026-10-15T11:00:00+00:00"
        self.save("beta", **NEW, records=[rec("b")], decisions=[{"decision": "db", "source": "s"}])
        for target in (None, "all", "alpha", "2026-10-15-beta"):
            with self.subTest(target=target):
                self.assertEqual([(d["project"], d["decision"]) for d in self.load(target)["decisions"]], [("alpha", "da"), ("beta", "db")])
        self.assertEqual(self.names(self.load()), ["2026-10-15-beta.md"])

    def test_query_is_a_case_insensitive_literal_over_title_and_description(self):
        self.save(**NEW, records=[rec("Alpha", "a.c literal"), rec("Beta", "abc")], decisions=[{"decision": "d", "source": "s"}])
        records = lambda q: [r["title"] for f in self.load("all", q)["files"] for r in f["records"]]
        self.assertEqual(records("ALPHA"), ["Alpha"])
        self.assertEqual(records("a.c"), ["Alpha"])
        self.assertEqual(records("abc"), ["Beta"])

    def test_query_drops_files_without_a_match_but_keeps_decisions(self):
        self.craft("2026-10-14-other.md", "2026-10-14T08:00:00+00:00", [stored_rec("O1", "Jiné", "nic")])
        self.save(**NEW, records=[rec("Alpha")], decisions=[{"decision": "d", "source": "s"}])
        out = self.load("all", "alpha")
        self.assertEqual(self.names(out), ["2026-10-15-proj.md"])
        self.assertEqual(len(out["decisions"]), 1)
        self.assertEqual(self.load("all", "zzz")["files"], [])

    def test_a_replaced_record_is_marked_superseded_by_its_successor(self):
        self.craft("2026-10-01-proj.md", "2026-10-01T10:00:00+00:00", [stored_rec("R1", "Stará", "původní", "2026-10-01T10:00:00+00:00")])
        new_id = self.save(**NEW, records=[rec("Nová", "oprava", supersedes="R1")])["added"][0]
        records = {r["id"]: r for f in self.load("all")["files"] for r in f["records"]}
        self.assertEqual(records["R1"]["superseded_by"], new_id)
        self.assertNotIn("superseded_by", records[new_id])

    def test_only_the_files_that_are_read_must_be_valid(self):
        self.dir.mkdir()
        (self.dir / "2026-10-10-bad.md").write_text("---\nschema_version: 99\nrevision: 1\n---\n")
        self.craft("2026-10-16-ok.md", "2026-10-16T08:00:00+00:00")
        self.assertEqual(self.names(self.load()), ["2026-10-16-ok.md"])
        with self.assertRaisesRegex(m.StoreError, "unknown schema"):
            self.load("all")
        self.assertTrue((self.dir / "2026-10-10-bad.md").read_text().startswith("---\nschema_version: 99"))

    def test_a_corrupt_decisions_log_makes_load_fail_loudly(self):
        self.dir.mkdir()
        (self.dir / "decisions.jsonl").write_text("not json\n")
        with self.assertRaisesRegex(m.StoreError, "line 1"):
            self.load()


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "Můj Projekt"
        self.project.mkdir()

    def run_cli(self, *args, cwd=None):
        p = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPT), *args], cwd=cwd or self.project, capture_output=True, text=True)
        return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr.strip()

    def proposal(self, **kw):
        path = self.root / "proposal.yaml"
        path.write_text(yaml.safe_dump(kw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return str(path)

    def save(self, *extra, revision=0, **kw):
        return self.run_cli("save", "--input", self.proposal(**kw), "--expected-revision", str(revision), *extra)

    def test_save_defaults_to_sessions_in_the_working_directory_and_the_folder_name(self):
        rc, out, err = self.save(scope="s", coverage="c", records=[rec()], decisions=[{"decision": "d", "source": "s"}])
        self.assertEqual((rc, err), (0, ""))
        today = out["path"].rsplit("/", 1)[1][:10]
        self.assertEqual(sorted(p.name for p in (self.project / "sessions").iterdir()), [f"{today}-muj-projekt.md", "decisions.jsonl"])
        self.assertEqual(out["decisions"], {"added": 1, "unchanged": 0})

    def test_load_modes_through_the_command_line(self):
        self.save("--project", "alpha", scope="s", coverage="c", records=[rec("a")], decisions=[{"decision": "d", "source": "s"}])
        self.save("--project", "beta", scope="s", coverage="c", records=[rec("b")])
        rc, out, err = self.run_cli("load")
        self.assertEqual((rc, len(out["files"]), len(out["decisions"])), (0, 1, 1))
        rc, out, err = self.run_cli("load", "all")
        self.assertEqual(sorted(f["project"] for f in out["files"]), ["alpha", "beta"])
        rc, out, err = self.run_cli("load", "alpha")
        self.assertEqual([f["project"] for f in out["files"]], ["alpha"])
        rc, out, err = self.run_cli("load", "all", "--query", "b")
        self.assertEqual([f["project"] for f in out["files"]], ["beta"])

    def test_store_dir_overrides_the_default_location(self):
        elsewhere = self.root / "elsewhere"
        rc, out, err = self.save("--store-dir", str(elsewhere), "--project", "p", scope="s", coverage="c", records=[rec()])
        self.assertEqual(rc, 0)
        self.assertTrue(any(elsewhere.glob("*-p.md")))
        self.assertFalse((self.project / "sessions").exists())
        rc, out, err = self.run_cli("load", "--store-dir", str(elsewhere))
        self.assertEqual(len(out["files"]), 1)

    def test_errors_exit_with_1_a_message_on_stderr_and_no_stdout(self):
        self.save("--project", "p", scope="s", coverage="c", records=[rec()])
        rc, out, err = self.save("--project", "p", scope="s", coverage="c", records=[rec("jiný")], revision=0)
        self.assertEqual((rc, out), (1, None))
        self.assertTrue(err.startswith("error: ") and "conflict" in err)
        rc, out, err = self.run_cli("load", "nonexistent")
        self.assertEqual((rc, out), (1, None))
        self.assertTrue(err.startswith("error: "))

    def test_a_project_name_that_looks_like_an_option_is_still_validated(self):
        rc, out, err = self.save("--project=-x", scope="s", coverage="c", records=[rec()])
        self.assertEqual(rc, 1)
        self.assertIn("slug", err)

    def test_expected_revision_is_required(self):
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), "save", "--input", self.proposal(records=[])], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--expected-revision", p.stderr)

    def test_load_on_an_empty_folder_prints_an_empty_result_and_creates_nothing(self):
        rc, out, err = self.run_cli("load")
        self.assertEqual((rc, out["files"], out["decisions"]), (0, [], []))
        self.assertFalse((self.project / "sessions").exists())


class SkillDocsTest(unittest.TestCase):
    """SKILL.md must keep describing what the script actually offers."""

    @classmethod
    def setUpClass(cls):
        cls.doc = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_front_matter_names_the_skill(self):
        front = yaml.safe_load(re.match(r"---\n(.*?)\n---\n", self.doc, re.S).group(1))
        self.assertEqual(front["name"], "memoria")
        self.assertTrue(front["description"].strip())

    def test_every_command_line_option_is_documented(self):
        for option in ("--store-dir", "--project", "--input", "--expected-revision", "--query"):
            with self.subTest(option=option):
                self.assertIn(option, self.doc)

    def test_documents_the_three_load_modes_and_the_store_layout(self):
        for text in ("memory_store.py load\n", "memory_store.py load all", "memory_store.py load memorizace", "sessions/", "decisions.jsonl", "today_revisions", "supersedes", "<date>-<project>.md"):
            with self.subTest(text=text):
                self.assertIn(text, self.doc)

    def test_the_documented_script_exists(self):
        self.assertIn("scripts/memory_store.py", self.doc)
        self.assertTrue(SCRIPT.is_file())


if __name__ == "__main__":
    unittest.main()
