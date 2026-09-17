"""Provider context from the effective canonical transcript after a rebase."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def rebuild_effective_messages(database, session_id: str, incoming: list) -> list:
    rows = database.get_chat_canonical_messages(session_id)
    result, identities = [], set()
    for row in rows:
        metadata = row.get("metadata") or {}
        identities.update(filter(None, (row["id"], metadata.get("clientMessageId"))))
        kwargs = {"v8_ingress_history": True, "canonicalMessageId": row["id"], "messageVersion": row["version"]}
        content = str(row.get("content_text") or "")
        if row["role"] == "user":
            import json
            from core.multimodal_payload_adapter import build_multimodal_content
            attachments = [a for a in metadata.get("attachments", []) if isinstance(a, dict)]
            if not attachments:
                attachments = [{"url": url, "mimeType": "image/*"} for url in metadata.get("images", []) if isinstance(url, str)]
            blocks = [{"type": "text", "text": content}]
            references = []
            for attachment in attachments:
                url = str(attachment.get("publicUrl") or attachment.get("url") or "")
                mime = str(attachment.get("mimeType") or attachment.get("type") or "")
                if mime.startswith("image/") and url.startswith(("https://", "http://", "data:image/")):
                    blocks.extend(build_multimodal_content(prompt="", media_url=url, mime_type=mime)[1:])
                else:
                    # Locally governed files remain references for the existing
                    # read tools; do not claim their bytes were sent as images.
                    references.append({k: attachment[k] for k in ("sourceId", "url", "workspacePath", "name", "mimeType") if k in attachment})
            if references:
                blocks.append({"type": "text", "text": "[Historical user source references; use the governed read tools when needed]\n" + json.dumps(references, ensure_ascii=False)})
            result.append(HumanMessage(content=blocks if attachments else content, id=row["id"],
                                       additional_kwargs={**kwargs, "sourceAttachments": attachments} if attachments else kwargs))
        elif row["role"] == "assistant":
            if metadata.get("editedBy") == "user":
                result.append(HumanMessage(content="[User-authored correction to an earlier assistant message]\n" + content,
                                          id=row["id"], additional_kwargs={**kwargs, "editedBy": "user"}))
            else:
                result.append(AIMessage(content=content, id=row["id"], additional_kwargs=kwargs))
            # Only complete call/result pairs are valid provider history. They
            # are immutable observed facts, independent of the narrative edit.
            calls = {}
            for node in row.get("nodes") or []:
                call_id = node.get("toolCallId")
                if node.get("executionType") == "tool_call" and call_id:
                    calls[call_id] = node
                elif node.get("executionType") == "tool_result" and call_id in calls:
                    call = calls.pop(call_id)
                    import json
                    result.append(AIMessage(content="", id=f"{row['id']}:{call_id}:call",
                        tool_calls=[{"id": call_id, "name": call.get("toolName") or "tool", "args": call.get("args") if isinstance(call.get("args"), dict) else {}}],
                        additional_kwargs=kwargs))
                    result.append(ToolMessage(content=json.dumps(node.get("result"), ensure_ascii=False),
                        tool_call_id=call_id, id=f"{row['id']}:{call_id}:result", additional_kwargs=kwargs))
    latest = next((message for message in reversed(incoming) if isinstance(message, HumanMessage)
                   and (message.additional_kwargs or {}).get("v8_ingress_history")), None)
    if latest is not None and latest.id not in identities:
        result.append(latest)
    # Runtime control notices are produced by Engine, outside the client history.
    result.extend(message for message in incoming if not (getattr(message, "additional_kwargs", {}) or {}).get("v8_ingress_history"))
    return result
