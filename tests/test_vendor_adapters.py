from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np


def test_vidorag_vendor_namespace_does_not_shadow_aucmr_utils():
    repo_root = Path(__file__).resolve().parents[1]
    probe = """
from eval.vendor import vidorag_adapter
from eval.vendor.ViDoRAG import vidorag_agents
from utils.retrieval import retrieval_service
import utils

assert 'eval/vendor/ViDoRAG/utils' not in str(utils.__file__).replace('\\\\', '/')
assert callable(retrieval_service)
assert vidorag_agents.Seeker.__module__ == 'eval.vendor.ViDoRAG.vidorag_agents'
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_simple_slot_accepts_equivalent_claims_shape_and_keeps_citations():
    from eval.baselines import _enforce_evidence_whitelist, _normalize_slot_response

    normalized = _normalize_slot_response(
        "goal",
        {"claims": [{"content": "Customer places a bid.", "evidence_refs": ["P25"]}]},
    )
    normalized = _enforce_evidence_whitelist(normalized, ["P25"])
    assert normalized["raw_text"] == "Customer places a bid."
    assert normalized["claims"] == [
        {"content": "Customer places a bid.", "evidence_refs": ["P25"]}
    ]


def test_simple_slot_claims_shape_still_rejects_unknown_citations():
    from eval.baselines import _enforce_evidence_whitelist, _normalize_slot_response

    normalized = _normalize_slot_response(
        "goal",
        {"claims": [{"content": "Unsupported goal.", "evidence_refs": ["P999"]}]},
    )
    normalized = _enforce_evidence_whitelist(normalized, ["P25"])
    assert normalized["claims"] == []
    assert normalized["raw_text"] == ""


def test_list_slot_accepts_equivalent_raw_text_shape_with_citations():
    from eval.baselines import _enforce_evidence_whitelist, _normalize_slot_response

    normalized = _normalize_slot_response(
        "preconditions",
        {"raw_text": "The customer is signed in.", "evidence_refs": ["P4"]},
    )
    normalized = _enforce_evidence_whitelist(normalized, ["P4"])
    assert normalized["claims"] == [
        {"content": "The customer is signed in.", "evidence_refs": ["P4"]}
    ]


def test_lvlm_direct_simple_slot_accepts_claims_shape_without_fabricating_refs():
    from eval.baselines import _normalize_uncited_slot_response

    normalized = _normalize_uncited_slot_response(
        "goal", {"claims": [{"content": "Customer places a bid."}]},
    )
    assert normalized["raw_text"] == "Customer places a bid."
    assert normalized["claims"] == [
        {"content": "Customer places a bid.", "evidence_refs": []}
    ]


def test_vdocrag_page_images_are_naturally_sorted_without_duplicate_dirs(tmp_path):
    from eval.vendor.vdocrag_adapter import page_image_paths

    project = tmp_path / "demo"
    project.mkdir()
    for name in ("10.png", "2.png", "1.jpg", "ignore.txt"):
        (project / name).write_bytes(b"x")

    paths = page_image_paths(tmp_path, "demo", ["demo"])
    assert [path.name for path in paths] == ["1.jpg", "2.png", "10.png"]


def test_vdocrag_retrieval_uses_official_embedding_scores():
    from eval.vendor.vdocrag_adapter import retrieve_pages

    class Runtime:
        config = {}

        @staticmethod
        def encode_query(_query):
            return np.asarray([1.0, 0.0], dtype=np.float32)

    pages = [Path("1.png"), Path("2.png"), Path("3.png")]
    vectors = np.asarray([[0.1, 0.0], [0.9, 0.0], [0.4, 0.0]], dtype=np.float32)
    with patch("eval.vendor.vdocrag_adapter.get_runtime", return_value=Runtime()), patch(
        "eval.vendor.vdocrag_adapter.corpus_embeddings", return_value=(pages, vectors)
    ):
        assert retrieve_pages("slot query", "demo", ["demo"], 2) == ["2.png", "3.png"]


def test_vidorag_runs_released_seeker_inspector_feedback_loop():
    from eval.vendor.vidorag_adapter import official_agent_pages

    calls = []

    class Seeker:
        def __init__(self, _vlm):
            self.buffer_images = None

        def run(self, query=None, images_path=None, feedback=None):
            if query is not None:
                self.buffer_images = list(images_path[1:])
                calls.append(("initial", query))
                return [images_path[0]], "summary", "reason"
            calls.append(("feedback", feedback))
            chosen = [self.buffer_images[0]]
            self.buffer_images = self.buffer_images[1:]
            return chosen, "summary", "reason"

    class Inspector:
        def __init__(self, _vlm):
            self.buffer_images = []
            self.round = 0

        def run(self, _query, selected):
            self.buffer_images.extend(selected)
            self.round += 1
            if self.round == 1:
                return "seeker", "find the complementary page", self.buffer_images
            return "synthesizer", "verified facts", list(self.buffer_images)

    pages, information = official_agent_pages(
        "slot query", ["1.png", "2.png", "3.png"], 5,
        vlm=object(), agent_classes=(Seeker, Inspector),
    )
    assert pages == ["1.png", "2.png"]
    assert information == "verified facts"
    assert calls == [("initial", "slot query"), ("feedback", "find the complementary page")]


def test_vidorag_stops_when_inspector_has_no_more_candidates():
    from eval.vendor.vidorag_adapter import official_agent_pages

    class Seeker:
        def __init__(self, _vlm):
            self.buffer_images = None

        def run(self, query=None, images_path=None, feedback=None):
            self.buffer_images = []
            return [images_path[0]], None, None

    class Inspector:
        def __init__(self, _vlm):
            self.buffer_images = []

        def run(self, _query, selected):
            self.buffer_images.extend(selected)
            return "seeker", "need more", self.buffer_images

    pages, information = official_agent_pages(
        "slot query", ["1.png"], 3,
        vlm=object(), agent_classes=(Seeker, Inspector),
    )
    assert pages == ["1.png"]
    assert information == "need more"


def test_vidorag_slot_answer_uses_synthesizer_adapter_and_whitelist():
    from eval.vendor.vidorag_adapter import _generate_slot

    response = (
        '{"claims":['
        '{"content":"valid","evidence_refs":["P2"]},'
        '{"content":"invalid","evidence_refs":["P999"]}'
        ']}'
    )
    with patch("eval.vendor.vidorag_adapter._run_slot_synthesizer", return_value=response) as run:
        result = _generate_slot(
            "preconditions", "Checkout", "Customer", {}, ["2.png"],
            candidate_answer="verified", vlm=object(),
        )
    assert result is not None
    assert [claim["content"] for claim in result["claims"]] == ["valid"]
    assert result["claims"][0]["evidence_refs"] == ["P2"]
    assert "Inspector candidate information" in run.call_args.args[0]
