import tempfile
import unittest
from pathlib import Path

import numpy as np

from pipelines.text_index import (
    _mineru_subprocess_env,
    CaptionCache,
    chunk_pages,
    colbert_maxsim,
    find_content_list,
    merge_content_by_page,
    normalize_multivector,
    rows_for_chunk,
)


class TextIndexTests(unittest.TestCase):
    def test_mineru_proxy_scheme_is_normalized_without_mutating_source(self):
        source = {
            "ALL_PROXY": "socks://127.0.0.1:7897/",
            "HTTPS_PROXY": "https://proxy.example:8443",
            "NO_PROXY": "127.0.0.1",
        }
        child = _mineru_subprocess_env(source)
        self.assertEqual(child["ALL_PROXY"], "socks5://127.0.0.1:7897/")
        self.assertEqual(child["HTTPS_PROXY"], source["HTTPS_PROXY"])
        self.assertEqual(child["NO_PROXY"], source["NO_PROXY"])
        self.assertEqual(source["ALL_PROXY"], "socks://127.0.0.1:7897/")

    def test_merge_keeps_order_and_captions_visual_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "figure.png").write_bytes(b"image")
            page_dir = root / "pages"
            page_dir.mkdir()
            (page_dir / "1.png").write_bytes(b"page")
            seen = []

            def captioner(path, kind, language):
                seen.append((path.name, kind, language))
                return "generated figure description"

            pages = merge_content_by_page(
                [
                    {"page_idx": 0, "type": "text", "text": "before"},
                    {"page_idx": 0, "type": "image", "img_path": "figure.png", "image_caption": ["mineru caption"]},
                    {"page_idx": 0, "type": "text", "text": "after"},
                ],
                root, page_dir, "English", CaptionCache(root / "cache.json"), captioner,
            )
            self.assertEqual(
                pages[0]["text"],
                "before\ngenerated figure description\nmineru caption\nafter",
            )
            self.assertEqual(seen, [("figure.png", "image", "English")])

    def test_empty_page_uses_full_page_caption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            page_dir = root / "pages"
            page_dir.mkdir()
            (page_dir / "1.png").write_bytes(b"page")
            pages = merge_content_by_page(
                [{"page_idx": 0, "type": "text", "text": ""}],
                root, page_dir, "Chinese", CaptionCache(root / "cache.json"),
                lambda path, kind, language: f"{kind}-{language}",
            )
            self.assertEqual(pages[0]["text"], "page-Chinese")

    def test_declared_pdf_pages_extend_beyond_content_list(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for page in (1, 2):
                (root / f"{page}.png").write_bytes(b"page")
            pages = merge_content_by_page(
                [{"page_idx": 0, "type": "text", "text": "first"}],
                root, root, "English", CaptionCache(root / "cache.json"),
                lambda path, kind, language: f"caption-{path.stem}",
                total_pages=2,
            )
            self.assertEqual([page["text"] for page in pages], ["first", "caption-2"])

    def test_missing_mineru_image_fails_in_strict_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(FileNotFoundError):
                merge_content_by_page(
                    [{"page_idx": 0, "type": "image", "img_path": "missing.png"}],
                    root, root, "English", CaptionCache(root / "cache.json"),
                    lambda path, kind, language: "unused",
                )

    def test_table_body_and_footnote_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pages = merge_content_by_page(
                [{
                    "page_idx": 0,
                    "type": "table",
                    "table_caption": ["Revenue table"],
                    "table_body": "Year | Revenue\n2025 | 10",
                    "table_footnote": ["USD millions"],
                }],
                root, root, "English", CaptionCache(root / "cache.json"),
                lambda path, kind, language: "unused",
            )
            self.assertEqual(
                pages[0]["text"],
                "Revenue table\nYear | Revenue\n2025 | 10\nUSD millions",
            )

    def test_chunk_metadata_and_token_rows(self):
        pages = [{"page_num": 2, "page_path": "/pages/2.png", "text": "first sentence. second sentence."}]
        chunks = chunk_pages(pages, "doc", chunk_size=18)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual([item["chunk_id"] for item in chunks], list(range(len(chunks))))
        rows = rows_for_chunk(chunks[0], [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(rows[0]["text"], chunks[0]["chunk"])
        self.assertEqual(rows[0]["page_path"], "/pages/2.png")
        self.assertEqual(rows[1]["text"], "")
        self.assertEqual(rows[1]["page_path"], "")

    def test_normalize_multivector_unwraps_single_batch(self):
        self.assertEqual(
            normalize_multivector([[[1, 2], [3, 4]]]),
            [[1.0, 2.0], [3.0, 4.0]],
        )

    def test_colbert_maxsim(self):
        score = colbert_maxsim([[1, 0], [0, 1]], [[1, 0], [0.5, 0.5]])
        self.assertAlmostEqual(score, 1.5)

    def test_find_content_list_prefers_matching_auto_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            other = root / "other" / "auto" / "other_content_list.json"
            exact = root / "doc" / "auto" / "doc_content_list.json"
            other.parent.mkdir(parents=True)
            exact.parent.mkdir(parents=True)
            other.write_text("[]", encoding="utf-8")
            exact.write_text("[]", encoding="utf-8")
            self.assertEqual(find_content_list(root, "doc"), exact)

    def test_leading_slash_mineru_image_is_treated_as_content_relative(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            (root / "images" / "figure.png").write_bytes(b"image")
            pages = merge_content_by_page(
                [{"page_idx": 0, "type": "image", "img_path": "/images/figure.png"}],
                root, root, "English", CaptionCache(root / "cache.json"),
                lambda path, kind, language: path.name,
            )
            self.assertEqual(pages[0]["text"], "figure.png")
        self.assertEqual(
            normalize_multivector([np.asarray([[1, 2], [3, 4]])]),
            [[1.0, 2.0], [3.0, 4.0]],
        )


if __name__ == "__main__":
    unittest.main()
