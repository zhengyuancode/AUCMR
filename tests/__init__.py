"""
CREST 单元测试 — 验证 P0/P1 关键不变式。

可独立运行:
    python -m AUCMR.tests
或 pytest:
    python -m pytest AUCMR/tests/__init__.py -v -s

不依赖外部服务（Milvus / Qwen）。所有 LLM/LVLM 调用都用本地 callable 桩替换。
"""

import json
import logging
import os
import sys
import unittest

# 确保 AUCMR 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# 共享 stub helpers
# ============================================================

def _make_provider(llm_fn, lvlm_fn):
    """构造 ModelProvider 桩。"""
    from AUCMR.crest.agents import ModelProvider
    return ModelProvider(llm_call=llm_fn, lvlm_call=lvlm_fn)


def _stub_llm_factory(responses):
    """按调用顺序返回 responses 中的字符串；用尽后返回最后一个。"""
    state = {"i": 0}

    def _stub(prompt, image_paths, assistant, is_overlap):
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[i]

    return _stub


def _stub_lvlm_factory(responses):
    """LVLM 同 LLM。"""
    state = {"i": 0}

    def _stub(prompt, image_paths, assistant, is_overlap):
        i = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[i]

    return _stub


def _make_retrieval_fn(page_paths):
    """每次返回 [page_paths] 给所有 query。"""
    def _fn(queries, file_names, topk):
        return [list(page_paths[:topk]) for _ in queries]
    return _fn


# ============================================================
# 1. Schemas
# ============================================================

class TestSchemas(unittest.TestCase):
    """验证 dataclass + Cockburn 8-slot 结构。"""

    def test_cockburn_dependency_order(self):
        from AUCMR.crest.schemas import (
            CockburnSlot, COCKBURN_DEPENDENCY_ORDER, COCKBURN_SLOTS,
            SLOT_DESCRIPTIONS, SLOT_LEADING_QUESTIONS,
        )
        self.assertEqual(len(COCKBURN_SLOTS), 8)
        self.assertEqual(COCKBURN_DEPENDENCY_ORDER[0], CockburnSlot.GOAL)
        self.assertEqual(COCKBURN_DEPENDENCY_ORDER[1], CockburnSlot.PRIMARY_ACTOR)
        self.assertEqual(COCKBURN_DEPENDENCY_ORDER[-1], CockburnSlot.POSTCONDITIONS)
        self.assertEqual(len(SLOT_DESCRIPTIONS), 8)
        self.assertEqual(len(SLOT_LEADING_QUESTIONS), 8)

    def test_evidence_card_serialization(self):
        from AUCMR.crest.schemas import EvidenceCard
        card = EvidenceCard(
            card_id="EC-MF-001",
            content="Pilot enters PIN.",
            source_pages=[4],
            source_locations=[{"page": 4, "location": "paragraph 2"}],
            confidence=0.9,
        )
        d = card.to_dict()
        self.assertEqual(d["card_id"], "EC-MF-001")
        self.assertEqual(d["source_pages"], [4])

    def test_cross_page_card(self):
        from AUCMR.crest.schemas import EvidenceCard
        card = EvidenceCard(
            card_id="EC-MF-007",
            content="State machine across pages 42-43.",
            source_pages=[42, 43],
            source_locations=[],
            is_cross_page=True,
            region_type="diagram_split",
        )
        self.assertTrue(card.is_cross_page)
        self.assertEqual(card.source_pages, [42, 43])

    def test_use_case_to_json_roundtrip(self):
        from AUCMR.crest.schemas import (
            CockburnSlot, SlotContent, UseCase, QualityFlag, RevisionRecord,
        )
        uc = UseCase(use_case_name="Test UC", actor="Pilot")
        uc.set_slot(CockburnSlot.GOAL, SlotContent(
            slot="goal", raw_text="Verify pilot identity.",
            evidence_card_ids=["EC-G-001"],
        ))
        uc.add_flag(QualityFlag(flag_type="no_evidence", slot="exception_flow"))
        uc.add_revision(RevisionRecord(round_num=0, action="initial_fill", revised_slots=["goal"]))

        data = json.loads(uc.to_json())
        self.assertEqual(data["use_case_name"], "Test UC")
        self.assertIn("goal", data["slots"])
        self.assertEqual(len(data["quality_flags"]), 1)
        # P1.2: scope_context 已删除，不再出现在序列化结果里
        self.assertNotIn("scope_context", data)

    def test_slot_content_quality_flags_not_serialized(self):
        """SlotContent.quality_flags 是 transient，不该出现在 to_dict 里。"""
        from AUCMR.crest.schemas import SlotContent, QualityFlag
        sc = SlotContent(slot="goal", raw_text="x", evidence_card_ids=["EC-G-001"])
        sc.quality_flags.append(QualityFlag(flag_type="evidence_invalid", slot="goal"))
        d = sc.to_dict()
        self.assertNotIn("quality_flags", d)


# ============================================================
# 2. Helpers (P0 修订相关)
# ============================================================

class TestHelpers(unittest.TestCase):

    def test_collect_invalid_refs(self):
        from AUCMR.crest.agents import _collect_invalid_refs
        from AUCMR.crest.schemas import EvidenceCard

        card_map = {"EC-X-001": EvidenceCard("EC-X-001", "c", [1], [])}
        data = {
            "claims": [
                {"content": "a", "evidence_refs": ["EC-X-001", "EC-X-999"]},
                {"content": "b", "evidence_refs": ["EC-X-002"]},
            ],
            "evidence_refs": ["EC-X-003"],
        }
        invalid = _collect_invalid_refs(data, card_map)
        self.assertEqual(invalid, {"EC-X-999", "EC-X-002", "EC-X-003"})

    def test_extract_page_num(self):
        from AUCMR.crest.agents import _extract_page_num
        self.assertEqual(_extract_page_num("data/images/jtv/14.png"), 14)
        self.assertEqual(_extract_page_num("page_0007.png"), 7)
        self.assertIsNone(_extract_page_num("/no/digits/here.png"))


# ============================================================
# 3. Convergence check
# ============================================================

class TestConvergence(unittest.TestCase):

    def test_changed_returns_true(self):
        from AUCMR.crest.agents import check_convergence
        from AUCMR.crest.schemas import CockburnSlot, SlotContent

        provider = _make_provider(
            llm_fn=_stub_llm_factory(['{"substantially_changed": true, "summary": "added a step"}']),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        v1 = SlotContent(slot="goal", raw_text="A", evidence_card_ids=["EC-G-001"])
        v2 = SlotContent(slot="goal", raw_text="A and B", evidence_card_ids=["EC-G-001", "EC-G-002"])
        self.assertTrue(check_convergence(v1, v2, CockburnSlot.GOAL, provider))

    def test_unchanged_returns_false(self):
        from AUCMR.crest.agents import check_convergence
        from AUCMR.crest.schemas import CockburnSlot, SlotContent

        provider = _make_provider(
            llm_fn=_stub_llm_factory(['{"substantially_changed": false, "summary": "no change"}']),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        v1 = SlotContent(slot="goal", raw_text="A", evidence_card_ids=["EC-G-001"])
        v2 = SlotContent(slot="goal", raw_text="A", evidence_card_ids=["EC-G-001"])
        self.assertFalse(check_convergence(v1, v2, CockburnSlot.GOAL, provider))


# ============================================================
# 4. Answer Generator — P0.1 (reject illegal evidence_refs)
# ============================================================

class TestAnswerGeneratorEvidenceBound(unittest.TestCase):
    """P0.1: claim 引用未知 EC-ID 时必须重试 → 仍未知则丢弃 + 打 flag。"""

    def _make_cards(self):
        from AUCMR.crest.schemas import EvidenceCard
        return [
            EvidenceCard("EC-G-001", "c1", [1], [], confidence=0.9),
            EvidenceCard("EC-G-002", "c2", [2], [], confidence=0.9),
        ]

    def test_unknown_refs_trigger_retry_then_drop(self):
        from AUCMR.crest.agents import answer_generator
        from AUCMR.crest.schemas import CockburnSlot, SlotContent

        # 第一次：声明含未知 EC-G-999；重试后仍含未知 → 该 claim 被丢弃 + 第二个合法 claim 保留
        provider = _make_provider(
            llm_fn=_stub_llm_factory([
                json.dumps({
                    "claims": [
                        {"content": "bad", "evidence_refs": ["EC-G-999"]},
                        {"content": "good", "evidence_refs": ["EC-G-001"]},
                    ]
                }),
                json.dumps({
                    "claims": [
                        {"content": "bad", "evidence_refs": ["EC-G-888"]},  # 重试仍未知
                        {"content": "good", "evidence_refs": ["EC-G-001"]},
                    ]
                }),
            ]),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        sc = answer_generator(
            actor="Pilot",
            use_case_name="Test",
            slot=CockburnSlot.GOAL,
            evidence_cards=self._make_cards(),
            filled_slots={},
            current_slot_content=SlotContent(slot="goal"),
            provider=provider,
        )
        # 只剩合法 claim
        self.assertEqual(len(sc.claims), 1)
        self.assertEqual(sc.claims[0].evidence_refs, ["EC-G-001"])
        # quality_flags 应记录 evidence_invalid
        self.assertTrue(any(f.flag_type == "evidence_invalid" for f in sc.quality_flags))

    def test_known_refs_pass_without_retry(self):
        from AUCMR.crest.agents import answer_generator
        from AUCMR.crest.schemas import CockburnSlot, SlotContent

        provider = _make_provider(
            llm_fn=_stub_llm_factory([
                json.dumps({
                    "claims": [{"content": "ok", "evidence_refs": ["EC-G-001"]}],
                }),
            ]),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        sc = answer_generator(
            actor="Pilot",
            use_case_name="Test",
            slot=CockburnSlot.GOAL,
            evidence_cards=self._make_cards(),
            filled_slots={},
            current_slot_content=SlotContent(slot="goal"),
            provider=provider,
        )
        self.assertEqual(len(sc.claims), 1)
        self.assertEqual(sc.quality_flags, [])

    def test_retry_recovers(self):
        """重试后所有引用变合法，不应打 flag。"""
        from AUCMR.crest.agents import answer_generator
        from AUCMR.crest.schemas import CockburnSlot, SlotContent

        provider = _make_provider(
            llm_fn=_stub_llm_factory([
                json.dumps({
                    "claims": [{"content": "x", "evidence_refs": ["EC-G-999"]}],
                }),
                json.dumps({
                    "claims": [{"content": "x", "evidence_refs": ["EC-G-001"]}],
                }),
            ]),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        sc = answer_generator(
            actor="Pilot",
            use_case_name="Test",
            slot=CockburnSlot.GOAL,
            evidence_cards=self._make_cards(),
            filled_slots={},
            current_slot_content=SlotContent(slot="goal"),
            provider=provider,
        )
        self.assertEqual(len(sc.claims), 1)
        self.assertEqual(sc.claims[0].evidence_refs, ["EC-G-001"])
        self.assertEqual(sc.quality_flags, [])


# ============================================================
# 5. Prompts — P0.3 (cross_ref removed)
# ============================================================

class TestPromptsNoCrossRef(unittest.TestCase):
    """P0.3: paging schema 不再允许 cross_ref。"""

    def test_paging_actions_schema_strings(self):
        from AUCMR.crest.prompts import _PAGING_ACTIONS_SCHEMA
        self.assertNotIn("cross_ref", _PAGING_ACTIONS_SCHEMA)
        self.assertIn("next", _PAGING_ACTIONS_SCHEMA)
        self.assertIn("previous", _PAGING_ACTIONS_SCHEMA)

    def test_resolve_cross_ref_function_removed(self):
        from AUCMR.crest import agents
        self.assertFalse(hasattr(agents, "_resolve_cross_ref"))


# ============================================================
# 6. Orchestrator — P0.4 (goal fail-fast)
# ============================================================

class TestGoalFailFast(unittest.TestCase):
    """P0.4: GOAL slot evidence 不足 → 立即返回 unresolvable_use_case，不跑后续 slot。"""

    def test_unresolvable_use_case_aborts_early(self):
        from AUCMR.crest.orchestrator import CRESTOrchestrator
        from AUCMR.crest.schemas import CockburnSlot

        # LLM 桩：QueryDesigner 第一次返回 [] → 直接收敛 → answer_generator 走 no_evidence 分支
        # 这样 goal slot 拿不到任何 evidence card，触发 fail-fast
        provider = _make_provider(
            llm_fn=_stub_llm_factory(["[]"]),  # 空 query → loop break
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        orchestrator = CRESTOrchestrator(
            retrieval_fn=_make_retrieval_fn([]),
            file_names=["doc"],
            topk=3,
            provider=provider,
            total_doc_pages=10,
            max_slot_rounds=1,
            goal_min_evidence_cards=1,
        )
        uc = orchestrator.elaborate(actor="Pilot", use_case_name="UnresolvableCase")

        # 应触发 unresolvable_use_case flag
        self.assertTrue(any(f.flag_type == "unresolvable_use_case" for f in uc.quality_flags))
        # GOAL 之外的 slot 不应被填充（PRIMARY_ACTOR 也跳过：早退在 GOAL 之后立即发生）
        non_goal_filled = [
            s for s, c in uc.slots.items()
            if s != CockburnSlot.GOAL.value and not c.is_empty()
        ]
        self.assertEqual(non_goal_filled, [])


# ============================================================
# 7. Orchestrator — Primary Actor 短路 + 跨 slot 透传 transient flags
# ============================================================

class TestOrchestratorBasics(unittest.TestCase):

    def test_primary_actor_shortcircuit(self):
        """PRIMARY_ACTOR slot 直接用 args.actor，不走 DCRRM loop。"""
        from AUCMR.crest.orchestrator import CRESTOrchestrator
        from AUCMR.crest.schemas import CockburnSlot

        # GOAL 通过：先返回 [] 收敛但提前注入一个 evidence card 让 fail-fast 通过
        # 简化：把 goal_min_evidence_cards 设 0 → fail-fast 关闭
        provider = _make_provider(
            llm_fn=_stub_llm_factory(["[]"]),
            lvlm_fn=_stub_lvlm_factory([""]),
        )
        orchestrator = CRESTOrchestrator(
            retrieval_fn=_make_retrieval_fn([]),
            file_names=["doc"],
            topk=1,
            provider=provider,
            total_doc_pages=10,
            max_slot_rounds=1,
            goal_min_evidence_cards=0,
            goal_min_confidence=0.0,
        )
        uc = orchestrator.elaborate(actor="Pilot", use_case_name="Test")
        actor_slot = uc.slots.get(CockburnSlot.PRIMARY_ACTOR.value)
        self.assertIsNotNone(actor_slot)
        self.assertEqual(actor_slot.raw_text, "Pilot")


class TestExperimentHelpers(unittest.TestCase):
    def test_baseline_whitelist_drops_unknown_refs(self):
        from AUCMR.eval.baselines import _enforce_evidence_whitelist

        normalized = {
            "slot": "goal",
            "claims": [
                {"content": "supported", "evidence_refs": ["P2"]},
                {"content": "invented", "evidence_refs": ["P99"]},
            ],
            "raw_text": "supported",
            "evidence_card_ids": ["P2", "P99"],
        }
        cleaned = _enforce_evidence_whitelist(normalized, ["P2", "P3"])
        self.assertEqual([c["content"] for c in cleaned["claims"]], ["supported"])
        self.assertEqual(cleaned["evidence_card_ids"], ["P2"])

    def test_lvlm_direct_uncited_normalization(self):
        from AUCMR.eval.baselines import _normalize_uncited_slot_response

        normalized = _normalize_uncited_slot_response(
            "main_flow",
            {"claims": [{"content": "Actor submits request", "evidence_refs": ["P7"]}]},
        )
        self.assertEqual(normalized["claims"][0]["evidence_refs"], [])

    def test_composite_builder_preserves_all_pages(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from AUCMR.utils.image_merge import build_composite_images

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = []
            for index in range(7):
                path = root / f"{index + 1}.png"
                Image.new("RGB", (80, 120), (index * 20, 0, 0)).save(path)
                pages.append(str(path))
            outputs = build_composite_images(
                pages, str(root / "merged"), composite_count=3,
                columns=2, page_width=40, jpeg_quality=70, gutter=2,
            )
            self.assertEqual(len(outputs), 3)
            self.assertTrue(all(Path(path).is_file() for path in outputs))

    def test_watermark_builder_preserves_numeric_page_stem(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from AUCMR.utils.image_merge import add_page_id_watermarks
        from AUCMR.eval.baselines import _page_number

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "17.png"
            Image.new("RGB", (160, 240), "white").save(source)
            outputs = add_page_id_watermarks([str(source)], str(root / "marked"))
            self.assertEqual(_page_number(outputs[0]), 17)


# ============================================================
# 入口
# ============================================================

def main():
    unittest.main(module=__name__, exit=False, verbosity=2)


if __name__ == "__main__":
    main()
