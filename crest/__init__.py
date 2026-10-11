"""
CREST — Cockburn-Reflective Elaboration with Source Tracing

AUCMR 的 elaboration 模块：给定用例名称 + 文档，生成 Cockburn 8-slot 用例，
每条 claim 可追溯到原始文档页面。

架构:
    Per-Slot DCRRM Loop (Cockburn dependency order):
        Query Designer → Evidence Collector → Answer Generator
      → Cross-Slot Consistency Revision (Final Phase)

Author: AUCMR Team
"""

from .schemas import (
    CockburnSlot,
    EvidenceCard,
    Claim,
    SlotContent,
    UseCase,
    QualityFlag,
    RevisionRecord,
    COCKBURN_SLOTS,
    COCKBURN_DEPENDENCY_ORDER,
    SLOT_DESCRIPTIONS,
    SLOT_LEADING_QUESTIONS,
)

from .orchestrator import CRESTOrchestrator

__all__ = [
    # Schemas
    "CockburnSlot",
    "EvidenceCard",
    "Claim",
    "SlotContent",
    "UseCase",
    "QualityFlag",
    "RevisionRecord",
    "COCKBURN_SLOTS",
    "COCKBURN_DEPENDENCY_ORDER",
    "SLOT_DESCRIPTIONS",
    "SLOT_LEADING_QUESTIONS",
    # Orchestrator
    "CRESTOrchestrator",
]
