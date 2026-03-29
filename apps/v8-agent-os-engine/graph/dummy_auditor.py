from typing import TypedDict, Sequence, Annotated
import operator
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END

class HookState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    agent_name: str
    hook_event: str
    hook_context: dict
    hook_rejected: bool
    hook_feedback: str

def dummy_auditor_node(state: HookState):
    """
    A simple dummy auditor agent that simply checks the context lengths
    or just prints. Used to verify the workflow hook system.
    """
    event = state.get("hook_event")
    context = state.get("hook_context", {})
    
    print(f"\n[DummyAuditorAgent] --- AUDIT STARTED ---")
    print(f"[DummyAuditorAgent] Inspecting Event: {event}")
    print(f"[DummyAuditorAgent] Context: {context}")
    
    # We simulate an auditing decision
    target_tool = context.get('tool', '')
    
    if target_tool == "dangerous_system_call":
        print(f"[DummyAuditorAgent] Detected dangerous tool: {target_tool}. Rejecting!")
        return {
            "hook_rejected": True,
            "hook_feedback": f"The '{target_tool}' is not permitted by security guidelines."
        }
    
    print(f"[DummyAuditorAgent] Audit passed. No issues found.")
    print(f"[DummyAuditorAgent] --- AUDIT COMPLETE ---\n")
    
    return {
        "hook_rejected": False,
        "hook_feedback": "Approved."
    }
    
# Build the graph
builder = StateGraph(HookState)
builder.add_node("auditor", dummy_auditor_node)
builder.add_edge(START, "auditor")
builder.add_edge("auditor", END)

# Export the compiled graph instance
compiled_graph = builder.compile()
