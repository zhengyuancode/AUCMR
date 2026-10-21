"""Fail-closed offline scope. Blocks network operations and legacy/model imports."""
from contextlib import contextmanager, ExitStack
import importlib.abc
import socket
import sys
from unittest.mock import patch

ALLOWED_INFRASTRUCTURE = {"eval.agent_evaluation", "eval.agent_evaluation.budget",
                          "eval.agent_evaluation.common"}
FORBIDDEN_IMPORTS = ("eval.l2_judge", "eval.compute_metrics", "eval.agent_evaluation",
                     "models", "torch", "transformers", "openai", "ollama")


class OfflineViolation(RuntimeError):
    pass


class _ImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ALLOWED_INFRASTRUCTURE:
            return None
        if any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN_IMPORTS):
            raise OfflineViolation("Forbidden offline import: " + fullname)
        return None


@contextmanager
def offline_guard():
    """Socket calls fail even if keys are configured. No subprocesses are used by this module."""
    audit = {"network_attempts_blocked": 0}
    def deny(*args, **kwargs):
        audit["network_attempts_blocked"] += 1
        raise OfflineViolation("Network access is prohibited in L2 offline execution")
    blocker = _ImportBlocker()
    sys.meta_path.insert(0, blocker)
    try:
        with ExitStack() as stack:
            for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
                stack.enter_context(patch.object(socket, name, deny))
            for name in ("connect", "connect_ex", "sendto", "send", "sendall", "sendmsg"):
                if hasattr(socket.socket, name):
                    stack.enter_context(patch.object(socket.socket, name, deny))
            yield audit
    finally:
        sys.meta_path.remove(blocker)
