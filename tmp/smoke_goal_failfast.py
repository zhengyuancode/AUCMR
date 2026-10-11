from crest.orchestrator import CRESTOrchestrator
from crest.schemas import Claim, EvidenceCard, SlotContent


orchestrator = CRESTOrchestrator.__new__(CRESTOrchestrator)
orchestrator.goal_min_evidence_cards = 1
orchestrator.goal_min_confidence = 0.3
orchestrator._goal_validation_diagnostic = {}

card_a = EvidenceCard("EC-G-001", "Customers browse products by category.", [19], [], confidence=0.9)
card_b = EvidenceCard("EC-G-002", "Customers search active auctions.", [81], [], confidence=0.9)
compound = SlotContent(
    slot="goal",
    claims=[Claim("The customer browses and searches auction products.", ["EC-G-001", "EC-G-002"])],
    evidence_card_ids=["EC-G-001", "EC-G-002"],
)
assert orchestrator._goal_is_resolvable(compound, [card_a, card_b]) is True

empty = SlotContent(slot="goal")
assert orchestrator._goal_is_resolvable(empty, [card_a]) is False
assert orchestrator._goal_validation_diagnostic["category"] == "empty_goal"

uncited = SlotContent(slot="goal", claims=[Claim("A goal.", ["UNKNOWN"])])
assert orchestrator._goal_is_resolvable(uncited, [card_a]) is False
assert orchestrator._goal_validation_diagnostic["category"] == "uncited_goal"

low = EvidenceCard("EC-G-LOW", "Weak evidence.", [1], [], confidence=0.1)
low_goal = SlotContent(
    slot="goal", claims=[Claim("A weak goal.", ["EC-G-LOW"])], evidence_card_ids=["EC-G-LOW"],
)
assert orchestrator._goal_is_resolvable(low_goal, [low]) is False
assert orchestrator._goal_validation_diagnostic["category"] == "low_confidence"

print("GOAL_FAILFAST_BOUNDARY_SMOKE_PASS")
