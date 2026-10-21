"""Integration with the actual read-only L3 SourceAccess implementation."""
import base64
import socket
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import fitz
from eval.l4_agent.protocol import L4Error, REPO, file_hash, network_blocked, new_run
from eval.l4_agent.sources import SourceTools
from eval.l4_agent.records import validate_refs


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = network_blocked()
        cls.network.__enter__()
        cls.addClassCleanup(cls.network.__exit__, None, None, None)
        cls.run_dir = new_run("test-source")
        pdf_path = cls.run_dir / "fixture.pdf"
        with fitz.open() as pdf:
            for text in ("FIXTURE NOT_RESEARCH_DATA. Save the file.", "FIXTURE NOT_RESEARCH_DATA. Report a write error."):
                page = pdf.new_page(width=320, height=200)
                page.insert_text((20, 30), text, fontsize=9)
                page.draw_rect(fitz.Rect(20, 50, 150, 100), color=(1, 0, 0))
            pdf.save(pdf_path)
        cls.source = {"source_id": "SRC", "project_id": "fixture", "path": pdf_path.relative_to(REPO).as_posix(),
                      "sha256": file_hash(pdf_path), "pages": 2, "state": "READY"}

    def tools(self, **changes):
        policy = {"search_max": 2, "pages_max": 2, "request_rounds": 4, "tools_per_round": 4}
        policy.update(changes)
        return SourceTools([self.source], "fixture", policy, self.run_dir / (uuid4().hex + ".jsonl"), "fixture")

    def test_index_text_search_and_original_image(self):
        tools = self.tools()
        index = tools.invoke("source_index", {"source_id": "SRC"})
        self.assertEqual(index["pages"], 2)
        found = tools.invoke("source_search", {"source_id": "SRC", "query": "write error"})
        self.assertEqual(found["hits"][0]["pdf_page"], 2)
        page = tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1})
        self.assertIn("Save the file", page["text"])
        image = tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1, "image": True})
        self.assertTrue(base64.b64decode(image["image_base64"]).startswith(b"\x89PNG"))
        self.assertNotIn("text", image)
        validate_refs([{"source_id": "SRC", "pdf_page": 1, "access_id": image["access_id"], "kind": "IMAGE", "description": "Red outlined box on the original page"}], tools.entries)
        self.assertTrue(all(e["source_sha256"] == self.source["sha256"] for e in tools.entries))

    def test_no_match_and_wrong_source_or_page(self):
        tools = self.tools()
        self.assertEqual(tools.invoke("source_search", {"source_id": "SRC", "query": "nonexistent claim"})["status"], "NO_MATCH")
        self.assertEqual(tools.invoke("source_page", {"source_id": "../../private", "pdf_page": 1})["status"], "UNAUTHORIZED_SOURCE")
        for page in (0, 3, "1", True):
            self.assertEqual(tools.invoke("source_page", {"source_id": "SRC", "pdf_page": page})["status"], "INVALID_PAGE")

    def test_page_and_search_budgets(self):
        tools = self.tools(search_max=1, pages_max=1)
        self.assertEqual(tools.invoke("source_search", {"source_id": "SRC", "query": "Save"})["status"], "OK")
        self.assertEqual(tools.invoke("source_search", {"source_id": "SRC", "query": "error"})["status"], "BUDGET_EXHAUSTED")
        self.assertEqual(tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1})["status"], "OK")
        self.assertEqual(tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 2})["status"], "BUDGET_EXHAUSTED")
        self.assertEqual(tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1, "image": True})["status"], "OK")

    def test_unreadable_page_and_image_do_not_fallback(self):
        tools = self.tools()
        with patch("fitz.open", side_effect=RuntimeError("test damaged PDF")):
            for image in (False, True):
                result = tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1, "image": image})
                self.assertEqual(result["status"], "PAGE_UNREADABLE")
                self.assertNotIn("text", result)
                self.assertNotIn("image_base64", result)

    def test_changed_source_stops_access(self):
        tools = self.tools()
        with patch("eval.l4_agent.sources.file_hash", return_value="changed-hash"):
            result = tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1})
        self.assertEqual(result["status"], "SOURCE_CHANGED")
        self.assertTrue(tools.integrity_failure)

    def test_image_cannot_be_claimed_from_text_access(self):
        tools = self.tools()
        result = tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1})
        with self.assertRaises(ValueError):
            validate_refs([{"source_id": "SRC", "pdf_page": 1, "access_id": result["access_id"], "kind": "IMAGE", "description": "box"}], tools.entries)

    def test_no_extra_crossref_or_arbitrary_arguments(self):
        tools = self.tools()
        self.assertEqual(tools.invoke("source_page", {"source_id": "SRC", "pdf_page": 1, "crossref": {}})["status"], "INVALID_ARGUMENTS")
        self.assertEqual(tools.invoke("source_index", {"source_id": "SRC", "path": "secret"})["status"], "INVALID_ARGUMENTS")
        self.assertEqual(tools.invoke("load_url", {"source_id": "SRC"})["status"], "INVALID_TOOL")

    def test_network_connect_and_udp_blocked(self):
        with socket.socket() as sock:
            with self.assertRaisesRegex(L4Error, "NETWORK_BLOCKED"):
                sock.connect(("127.0.0.1", 80))
        with socket.socket(type=socket.SOCK_DGRAM) as sock:
            with self.assertRaisesRegex(L4Error, "NETWORK_BLOCKED"):
                sock.sendto(b"blocked", ("127.0.0.1", 53))


if __name__ == "__main__":
    unittest.main()
