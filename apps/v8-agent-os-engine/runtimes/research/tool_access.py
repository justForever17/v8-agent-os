"""Read-only projection of the existing Research broker, without activation."""

from typing import Annotated, Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState


@tool("research_broker")
def saved_research_reader(
    mode: Literal["observe", "search_experience", "get_experience", "get_evidence"],
    query: str = "",
    experiencePackId: str = "",
    evidenceBundleId: str = "",
    readAnswer: bool = False,
    sourceKey: str = "",
    startChar: int = 0,
    maxChars: int = 6000,
    limit: int = 20,
    includeArchived: bool = False,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Read saved Research answers and original evidence without running research.

    Reads use the current session's owner/workspace permissions. A known ID or
    inherited text never grants access to another user's or workspace's data.

    Use search_experience(query) to find a saved answer, get_experience(experiencePackId)
    to resolve its current evidenceBundleId, and get_evidence(evidenceBundleId,
    readAnswer=true) for the complete answer, limitations and citation directory.
    Begin the full document at startChar=0, even after a preview. Copy the returned
    nextOffset exactly until null; do not calculate startChar + maxChars yourself.
    readAnswer reads the reviewed narrative, not independent source verification;
    the original claimId index follows the answer. Use sourceKey to open each
    relevant original snapshot body for independent verification.
    sourceKey='S1' reads an original source
    snapshot instead of the answer. Memory summaries are only pointers, not the
    saved answer or evidence that a new verification task has executed.
    Pass exact evidence refs and runtimeAccess=['research.read'] to a delegated
    verifier that needs these reads; this grants no new research or mutation.
    For authorized answer updates route through runtime_broker(kind='research',
    experiencePackId=<saved id>); this read surface never starts a model or search.
    """
    from core.tools.research_broker import research_broker

    if mode not in {"observe", "search_experience", "get_experience", "get_evidence"}:
        import json
        from runtimes.research.access_scope import research_access_denied

        return json.dumps(research_access_denied(str(mode)), ensure_ascii=False)

    return research_broker.func(
        mode=mode, query=query, experiencePackId=experiencePackId,
        evidenceBundleId=evidenceBundleId, readAnswer=readAnswer,
        sourceKey=sourceKey, startChar=startChar, maxChars=maxChars,
        limit=limit, includeArchived=includeArchived, state=state,
    )
