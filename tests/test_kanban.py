"""
Tests for the kanban module: service layer, dedup, Notion CSV import, MCP tools.
Uses an in-memory SQLite DB built by migrate_kanban() — no production DB touched.
"""
from __future__ import annotations

import json
import sqlite3
import unittest

from app.migrate import migrate_kanban
from app.services import kanban as kb
from app.services import kanban_import as ki


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        migrate_kanban(conn)
    return conn


def _col(conn, name, slug="job-search"):
    board = kb.get_board_row(conn, slug)
    return kb.find_column(conn, board["id"], name)


def _order(conn, column_name):
    col = _col(conn, column_name)
    return [r["title"] for r in conn.execute(
        "SELECT title FROM kanban_cards WHERE column_id=? AND archived_at IS NULL ORDER BY position",
        (col["id"],),
    )]


class TestSeedAndMigration(unittest.TestCase):
    def test_seeded_board_and_idempotent(self):
        conn = _make_db()
        with conn:
            migrate_kanban(conn)  # second run must not duplicate anything
        boards = kb.list_boards(conn)
        self.assertEqual([b["slug"] for b in boards], ["job-search"])
        cols = boards[0]["columns"]
        self.assertEqual([c["name"] for c in cols],
                         ["New", "Interested", "Applied", "Interviewing", "Offer", "Closed"])
        self.assertEqual([c["is_inbox"] for c in cols], [1, 0, 0, 0, 0, 0])


class TestDedup(unittest.TestCase):
    def test_canonical_url_drops_tracking_keeps_identity(self):
        a = kb.canonical_url("https://www.indeed.com/viewjob?jk=abc123&from=serp&utm_source=x")
        b = kb.canonical_url("http://indeed.com/viewjob/?jk=abc123")
        c = kb.canonical_url("https://www.indeed.com/viewjob?jk=zzz999")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(
            kb.canonical_url("https://www.linkedin.com/jobs/view/42/?refId=x&trackingId=y"),
            "linkedin.com/jobs/view/42",
        )

    def test_key_priority_and_plain_tasks(self):
        self.assertTrue(kb.compute_dedup_key(url="https://x.com/j/1", external_id="9").startswith("url:"))
        self.assertEqual(kb.compute_dedup_key(source="Indeed", external_id="9"), "id:indeed:9")
        self.assertEqual(kb.compute_dedup_key(company=" Acme ", title="Dev", location="NYC"),
                         "job:acme|dev|nyc")
        self.assertIsNone(kb.compute_dedup_key(title="Call recruiter"))

    def test_batch_skips_existing_in_batch_and_archived(self):
        conn = _make_db()
        r1 = kb.add_cards_batch(conn, "job-search", [
            {"title": "Dev", "company": "Acme", "url": "https://acme.com/jobs/1?utm_campaign=z"},
            {"title": "Dev again", "company": "Acme", "url": "https://acme.com/jobs/1"},  # same job, same batch
            {"title": "PM", "source": "indeed", "external_id": "77"},
        ])
        self.assertEqual(len(r1["added"]), 2)
        self.assertEqual(r1["skipped"][0]["reason"], "duplicate")

        kb.set_archived(conn, r1["added"][0]["id"], True)
        r2 = kb.add_cards_batch(conn, "job-search", [
            {"title": "Dev", "url": "https://www.acme.com/jobs/1/"},
            {"title": "PM", "source": "Indeed", "external_id": "77"},
        ])
        self.assertEqual(r2["added"], [])
        self.assertEqual({s["existing_card_id"] for s in r2["skipped"]},
                         {r1["added"][0]["id"], r1["added"][1]["id"]})

    def test_plain_task_cards_never_collide(self):
        conn = _make_db()
        r = kb.add_cards_batch(conn, "job-search", [{"title": "Follow up"}, {"title": "Follow up"}])
        self.assertEqual(len(r["added"]), 2)

    def test_update_card_into_duplicate_rejected(self):
        conn = _make_db()
        r = kb.add_cards_batch(conn, "job-search", [
            {"title": "A", "url": "https://a.com/1"}, {"title": "B", "url": "https://b.com/2"},
        ])
        with self.assertRaises(kb.DuplicateCardError):
            kb.update_card(conn, r["added"][1]["id"], {"url": "https://a.com/1"})


class TestValidation(unittest.TestCase):
    def test_fit_score_and_title(self):
        conn = _make_db()
        r = kb.add_cards_batch(conn, "job-search", [
            {"title": "Too high", "fit_score": 6},
            {"title": "Zero", "fit_score": 0},
            {"title": "", "company": "NoTitle"},
            {"title": "Good", "fit_score": "4", "remote_type": "Remote"},
        ])
        self.assertEqual([a["title"] for a in r["added"]], ["Good"])
        self.assertEqual(len(r["skipped"]), 3)
        card = kb.get_card(conn, r["added"][0]["id"])
        self.assertEqual((card["fit_score"], card["remote_type"], card["created_by"]), (4, "remote", "mcp"))

    def test_db_check_constraint_backstops_fit_score(self):
        conn = _make_db()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO kanban_cards (board_id, column_id, title, fit_score) VALUES (1, 1, 'x', 9)"
            )


class TestOrdering(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()
        r = kb.add_cards_batch(self.conn, "job-search", [{"title": t} for t in "ABCD"])
        self.ids = {a["title"]: a["id"] for a in r["added"]}

    def test_batch_lands_in_inbox_sequentially(self):
        self.assertEqual(_order(self.conn, "New"), list("ABCD"))
        positions = [r[0] for r in self.conn.execute("SELECT position FROM kanban_cards ORDER BY id")]
        self.assertEqual(positions, [0, 1, 2, 3])

    def test_move_across_and_within_columns(self):
        applied = _col(self.conn, "Applied")["id"]
        new = _col(self.conn, "New")["id"]
        kb.move_card(self.conn, self.ids["B"], applied)
        kb.move_card(self.conn, self.ids["D"], applied, position=0)
        self.assertEqual(_order(self.conn, "Applied"), ["D", "B"])
        self.assertEqual(_order(self.conn, "New"), ["A", "C"])
        kb.move_card(self.conn, self.ids["C"], new, position=0)
        self.assertEqual(_order(self.conn, "New"), ["C", "A"])
        positions = [r[0] for r in self.conn.execute(
            "SELECT position FROM kanban_cards WHERE column_id=? ORDER BY position", (new,))]
        self.assertEqual(positions, [0, 1])

    def test_cannot_move_to_other_board(self):
        slug = kb.create_board(self.conn, "Business Tasks")
        other = kb.get_board(self.conn, slug)["columns"][0]["id"]
        with self.assertRaises(kb.KanbanError):
            kb.move_card(self.conn, self.ids["A"], other)

    def test_archive_and_inbox_count(self):
        self.assertEqual(kb.inbox_count(self.conn), 4)
        kb.set_archived(self.conn, self.ids["B"], True)
        self.assertEqual(kb.inbox_count(self.conn), 3)
        self.assertEqual(_order(self.conn, "New"), ["A", "C", "D"])
        kb.set_archived(self.conn, self.ids["B"], False)
        self.assertEqual(_order(self.conn, "New"), ["A", "C", "D", "B"])


class TestColumns(unittest.TestCase):
    def test_column_crud_guards(self):
        conn = _make_db()
        board_id = kb.get_board_row(conn, "job-search")["id"]
        with self.assertRaises(kb.KanbanError):
            kb.add_column(conn, board_id, "applied")  # case-insensitive dup
        cid = kb.add_column(conn, board_id, "Ghosted")
        kb.shift_column(conn, cid, -1)
        names = [c["name"] for c in kb.get_columns(conn, board_id)]
        self.assertEqual(names[-2:], ["Ghosted", "Closed"])
        with self.assertRaises(kb.KanbanError):
            kb.delete_column(conn, _col(conn, "New")["id"])  # inbox
        kb.delete_column(conn, cid)
        self.assertIsNone(_col(conn, "Ghosted"))


NOTION_CSV = (
    "﻿Name,Company,Status,Link,Fit,Salary,Priority\n"
    "Data Analyst,Acme,Applied,https://www.indeed.com/viewjob?jk=1&from=x,4/5,$80k,High\n"
    "BI Developer,Globex,Not started,https://globex.com/careers/2,⭐⭐⭐,,\n"
    "Ops Lead,Initech,,https://initech.com/j/3,great,,Low\n"
)


class TestNotionImport(unittest.TestCase):
    def test_auto_map(self):
        headers, rows = ki.parse_csv(NOTION_CSV)
        self.assertEqual(headers[0], "Name")
        self.assertEqual(len(rows), 3)
        m = ki.auto_map(headers)
        self.assertEqual(m, {
            "Name": "title", "Company": "company", "Status": "_column", "Link": "url",
            "Fit": "fit_score", "Salary": "salary_text", "Priority": "_append",
        })

    def test_import_maps_status_creates_columns_and_is_rerunnable(self):
        conn = _make_db()
        headers, rows = ki.parse_csv(NOTION_CSV)
        cards = ki.build_cards(rows, ki.auto_map(headers))
        self.assertEqual(cards[0]["fit_score"], 4)
        self.assertEqual(cards[1]["fit_score"], 3)
        self.assertIn("Fit: great", cards[2]["notes"])
        self.assertIn("Priority: Low", cards[2]["notes"])

        r = kb.add_cards_batch(conn, "job-search", cards, created_by="import",
                               create_missing_columns=True)
        self.assertEqual([a["column"] for a in r["added"]], ["Applied", "Not started", "New"])
        self.assertIsNotNone(_col(conn, "Not started"))

        again = kb.add_cards_batch(conn, "job-search", cards, created_by="import",
                                   create_missing_columns=True)
        self.assertEqual(again["added"], [])
        self.assertEqual(len(again["skipped"]), 3)


class TestMcpTools(unittest.TestCase):
    def test_tools_round_trip_json(self):
        from unittest.mock import patch
        from fastmcp import FastMCP
        from app.mcp import kanban_tools

        import tempfile
        from pathlib import Path

        # Tools open and close their own connections, so use a temp file DB.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "kb.db"

        def conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            return c

        setup = conn_factory()
        with setup:
            migrate_kanban(setup)
        setup.close()

        mcp = FastMCP("test")
        captured = {}
        orig_tool = mcp.tool

        def grab(*a, **kw):
            deco = orig_tool(*a, **kw)
            def inner(fn):
                captured[fn.__name__] = fn
                return deco(fn)
            return inner

        with patch.object(mcp, "tool", grab):
            kanban_tools.register(mcp)

        with patch("app.mcp.kanban_tools.get_conn", side_effect=conn_factory):
            JobCard = kanban_tools.JobCard
            res = captured["add_job_cards"](board="job-search", jobs=[
                JobCard(title="Analyst", company="Acme", url="https://acme.com/1", fit_score=5),
                JobCard(title="Analyst", company="Acme", url="https://acme.com/1"),
            ])
            self.assertEqual((res["added_count"], res["skipped_count"]), (1, 1))
            card_id = res["added"][0]["id"]

            captured["update_card"](card_id=card_id, notes="Great benefits")
            captured["move_card"](card_id=card_id, column_name="interested")
            listed = captured["get_board_cards"](board="job-search", column="Interested")
            self.assertEqual(listed["cards"][0]["notes"], "Great benefits")
            boards = captured["list_boards"]()
            for out in (res, listed, boards):
                json.dumps(out)
            with self.assertRaises(ValueError):
                captured["move_card"](card_id=card_id, column_name="Nope")


if __name__ == "__main__":
    unittest.main()
