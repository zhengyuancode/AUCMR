"""L4 audit/status wrapper around the unchanged L3 local PDF tools."""
from __future__ import annotations

from pathlib import Path
from eval.agent_evaluation.source_access import SourceAccess
from eval.agent_evaluation.common import Blocked, EvaluationError
from .inputs import local_path
from .protocol import file_hash


class SourceTools:
    def __init__(self, sources: list[dict], project: str, policy: dict, log_path: Path, session_id: str):
        self.sources = {s["source_id"]: s for s in sources if s["project_id"] == project}
        self.policy = dict(policy)
        self.entries = []
        self.integrity_failure = False
        self.backend = SourceAccess(
            [{**s, "pdf": str(local_path(s["path"]))} for s in self.sources.values()],
            project, "L4_AGENT", log_path, search_max=policy["search_max"],
            pages_max=policy["pages_max"], session_id=session_id)

    def invoke(self, name: str, arguments: dict) -> dict:
        result = None
        if not isinstance(arguments, dict):
            arguments = {}
        sid = arguments.get("source_id")
        if not isinstance(sid, str):
            sid = None
        try:
            if name not in {"source_index", "source_search", "source_page"}:
                result = {"status": "INVALID_TOOL"}
            elif sid not in self.sources:
                result = {"status": "UNAUTHORIZED_SOURCE"}
            elif len(self.entries) >= self.policy["request_rounds"] * self.policy["tools_per_round"]:
                result = {"status": "BUDGET_EXHAUSTED"}
            else:
                source = self.sources[sid]
                if file_hash(local_path(source["path"])) != source["sha256"]:
                    self.integrity_failure = True
                    result = {"status": "SOURCE_CHANGED"}
                elif name == "source_page" and (type(arguments.get("pdf_page")) is not int or
                        not 1 <= arguments["pdf_page"] <= source["pages"]):
                    result = {"status": "INVALID_PAGE"}
                elif name == "source_page" and ("crossref" in arguments or
                        ("image" in arguments and type(arguments["image"]) is not bool)):
                    result = {"status": "INVALID_ARGUMENTS"}
                else:
                    value = self.backend.invoke(name, arguments)
                    # The backend only returns source content and registered IDs, never paths.
                    value.pop("access_id", None)
                    result = {**value, "status": "NO_MATCH" if name == "source_search" and not value["hits"] else "OK"}
        except Blocked:
            result = {"status": "BUDGET_EXHAUSTED"}
        except (TypeError, KeyError, EvaluationError):
            result = {"status": "INVALID_ARGUMENTS"}
        except (OSError, RuntimeError, ValueError):
            result = {"status": "PAGE_UNREADABLE"}
        access_id = "L4-ACCESS-" + str(len(self.entries) + 1)
        result = {**result, "access_id": access_id, "data_is_untrusted": True}
        # Retain text/image payloads so citations can be revalidated without another model.
        self.entries.append({"access_id": access_id, "operation": name, "arguments": arguments,
                             "source_sha256": self.sources.get(sid, {}).get("sha256"), "result": result})
        return result
