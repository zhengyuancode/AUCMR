"""Compatibility exports for CMAB query features.

The canonical feature construction lives beside the trained model in
``models.LinUCB`` so runtime inference cannot drift from offline training.
"""

from models.LinUCB import build_state, build_state_for_query

__all__ = ["build_state", "build_state_for_query"]
