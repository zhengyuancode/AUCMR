"""
CREST 数据模式定义。

Cockburn 8-slot fully-dressed use case 模板:
    Goal → Primary Actor → Preconditions → Trigger
    → Main Flow → Alternative Flow → Exception Flow → Postconditions
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
import json


class CockburnSlot(Enum):
    """Cockburn 8-slot 模板的 slot 枚举。"""
    GOAL = "goal"
    PRIMARY_ACTOR = "primary_actor"
    PRECONDITIONS = "preconditions"
    TRIGGER = "trigger"
    MAIN_FLOW = "main_flow"
    ALTERNATIVE_FLOW = "alternative_flow"
    EXCEPTION_FLOW = "exception_flow"
    POSTCONDITIONS = "postconditions"


# Slot 的 Cockburn 依赖顺序（Y 阶段）
COCKBURN_DEPENDENCY_ORDER: List[CockburnSlot] = [
    CockburnSlot.GOAL,
    CockburnSlot.PRIMARY_ACTOR,
    CockburnSlot.PRECONDITIONS,
    CockburnSlot.TRIGGER,
    CockburnSlot.MAIN_FLOW,
    CockburnSlot.ALTERNATIVE_FLOW,
    CockburnSlot.EXCEPTION_FLOW,
    CockburnSlot.POSTCONDITIONS,
]

COCKBURN_SLOTS = [s.value for s in CockburnSlot]

# 每个 slot 的 Cockburn 定义
SLOT_DESCRIPTIONS: Dict[str, str] = {
    "goal": "The high-level objective of the use case — what the primary actor wants to achieve.",
    "primary_actor": "The stakeholder or external system that initiates the use case.",
    "preconditions": "System state and conditions that must hold before the use case can begin.",
    "trigger": "The event or user action that starts the use case.",
    "main_flow": "The step-by-step normal interaction sequence between actor(s) and system.",
    "alternative_flow": "Legal alternative paths that diverge from the main flow.",
    "exception_flow": "Error or unexpected condition handling paths.",
    "postconditions": "System state after the use case completes successfully.",
}

# 每个 slot 的引导问题（用于 Evidence Collector 的 slot-conditioned query）
SLOT_LEADING_QUESTIONS: Dict[str, str] = {
    "goal": "What is the user's high-level objective?",
    "primary_actor": "Who initiates this use case?",
    "preconditions": "What system state and conditions must hold before this UC starts?",
    "trigger": "What event or user action starts this UC?",
    "main_flow": "What is the step-by-step normal interaction sequence?",
    "alternative_flow": "What legal alternative paths exist?",
    "exception_flow": "What happens when errors or unexpected conditions occur?",
    "postconditions": "What system state holds after the UC completes successfully?",
}


@dataclass
class EvidenceCard:
    """Evidence Collector 从一页或一个完备区域提取的证据卡片。

    每条卡片对应一个独立的证据片段，记录其来源页面和位置。
    """
    card_id: str                                    # 唯一 ID，如 "EC-MF-003"
    content: str                                     # 证据内容（一句话）
    source_pages: List[int]                          # 来源页面列表（单页或跨页区域）
    source_locations: List[Dict[str, Any]]           # 每页的定位信息
    is_cross_page: bool = False                      # 是否跨页
    region_type: str = "text"                        # text / table / diagram / diagram_split
    slot_relevance: str = ""                         # 为什么与当前 slot 相关
    confidence: float = 0.0                          # 0-1
    source_doc: str = ""                             # 来源文档标识（多 PDF 检索时区分同页号）

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_dict(self) -> dict:
        """返回用于提示词的裁剪版 dict：保留 content 完整，裁掉纯元数据字段。"""
        return {
            "card_id": self.card_id,
            "content": self.content,
            "source_pages": self.source_pages,
            "slot_relevance": self.slot_relevance,
            "confidence": self.confidence,
        }


@dataclass
class Claim:
    """Author 生成的一条 claim，必须引用至少一个 EvidenceCard。"""
    content: str                                     # claim 文本
    evidence_refs: List[str] = field(default_factory=list)  # 引用的 EvidenceCard.card_id
    step_number: Optional[int] = None                # 仅 Main Flow / Alternative Flow 等有序 slot 使用
    is_cross_page: bool = False                      # 是否来自跨页证据

    def to_dict(self) -> dict:
        d = {"content": self.content, "evidence_refs": self.evidence_refs}
        if self.step_number is not None:
            d["step_number"] = self.step_number
        if self.is_cross_page:
            d["is_cross_page"] = self.is_cross_page
        return d


@dataclass
class SlotContent:
    """一个 Cockburn slot 的完整内容。"""
    slot: str                                        # CockburnSlot.value
    claims: List[Claim] = field(default_factory=list)  # 结构化内容
    raw_text: str = ""                               # 自由文本版本（Goal / Trigger 等简单 slot 用）
    evidence_card_ids: List[str] = field(default_factory=list)  # 所有引用的 card_id
    # Goal 槽产生的内部场景边界；约束后续检索/写作，不参与 claim 计数。
    scope_contract: Dict[str, Any] = field(default_factory=dict)
    # 缺口驱动循环的内部覆盖状态；保留在输出中用于审计和恢复。
    covered_aspect_ids: List[str] = field(default_factory=list)
    missing_aspects: List[str] = field(default_factory=list)
    aspect_status_known: bool = False
    # transient: 由 answer_generator 在 P0.1 evidence-invalid 检测时填入，
    # 由 orchestrator 收集到 UseCase.quality_flags 后清空；不进 to_dict。
    quality_flags: List["QualityFlag"] = field(default_factory=list)
    # transient audit snapshot: the author's structured candidate before the
    # deterministic evidence-reference gate drops/repairs unsupported claims.
    # It deliberately stays out of the final slot payload; UseCase.to_dict()
    # emits it once under citation_audit for CitCov calculation.
    pre_constraint_draft: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"slot": self.slot}
        if self.claims:
            d["claims"] = [c.to_dict() for c in self.claims]
        if self.raw_text:
            d["raw_text"] = self.raw_text
        if self.evidence_card_ids:
            d["evidence_card_ids"] = self.evidence_card_ids
        if self.scope_contract:
            d["scope_contract"] = self.scope_contract
        if self.aspect_status_known:
            d["coverage_state"] = {
                "covered_aspect_ids": self.covered_aspect_ids,
                "missing_aspects": self.missing_aspects,
            }
        return d

    def is_empty(self) -> bool:
        return not self.claims and not self.raw_text


@dataclass
class QualityFlag:
    """最终输出的质量标记。"""
    flag_type: str                                   # no_evidence / low_confidence / unresolved_inconsistency
    slot: Optional[str] = None
    detail: str = ""
    evidence_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RevisionRecord:
    """记录一次修订操作。"""
    round_num: int
    action: str                                      # initial_fill / slot_revision / consistency_revision
    revised_slots: List[str] = field(default_factory=list)
    inconsistency_hints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UseCase:
    """CREST 输出的完整 Cockburn use case。"""
    use_case_name: str
    actor: str = ""                                  # primary actor
    slots: Dict[str, SlotContent] = field(default_factory=dict)
    quality_flags: List[QualityFlag] = field(default_factory=list)
    revision_history: List[RevisionRecord] = field(default_factory=list)
    all_evidence_cards: List[EvidenceCard] = field(default_factory=list)
    evidence_summary: Dict[str, Any] = field(default_factory=dict)
    runtime_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        audit_slots = {
            name: content.pre_constraint_draft
            for name, content in self.slots.items()
            if content.pre_constraint_draft
        }
        return {
            "use_case_name": self.use_case_name,
            "actor": self.actor,
            "slots": {k: v.to_dict() for k, v in self.slots.items()},
            "quality_flags": [f.to_dict() for f in self.quality_flags],
            "revision_history": [r.to_dict() for r in self.revision_history],
            "evidence_summary": self.evidence_summary,
            "runtime_stats": self.runtime_stats,
            "citation_audit": {
                "version": "pre_constraint_draft_v1",
                "description": (
                    "Structured author candidates captured before deterministic "
                    "evidence-reference validation. Final slots remain evidence-valid."
                ),
                "pre_constraint_slots": audit_slots,
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def get_slot(self, slot: CockburnSlot) -> Optional[SlotContent]:
        return self.slots.get(slot.value)

    def set_slot(self, slot: CockburnSlot, content: SlotContent):
        self.slots[slot.value] = content

    def add_evidence_cards(self, cards: List[EvidenceCard]):
        self.all_evidence_cards.extend(cards)

    def add_flag(self, flag: QualityFlag):
        self.quality_flags.append(flag)

    def add_revision(self, record: RevisionRecord):
        self.revision_history.append(record)
