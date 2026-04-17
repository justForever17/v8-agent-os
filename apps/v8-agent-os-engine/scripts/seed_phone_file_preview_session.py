from __future__ import annotations

import mimetypes
import sys
import time
import uuid
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.database import db  # noqa: E402
from core.scoped_workspace_resource import build_workspace_resource_ref  # noqa: E402
from core.workspace_resolution import workspace_resolution_service  # noqa: E402
from erc.chat_canonical_transcript import CanonicalTranscriptBuilder  # noqa: E402


def _mime_type(path: Path) -> str:
    if path.suffix.lower() == ".glb":
        return "model/gltf-binary"
    if path.suffix.lower() == ".gltf":
        return "model/gltf+json"
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _artifact_node(message_id: str, root: Path, relative_path: str, index: int) -> dict:
    absolute_path = root / Path(*relative_path.split("/"))
    mime_type = _mime_type(absolute_path)
    resource_ref = build_workspace_resource_ref(
        workspace_relative_path=relative_path,
        path_plane="workspace_artifact",
        workspace_root=root,
        mime_type=mime_type,
        display_label=absolute_path.name,
        previewable=True,
        downloadable=True,
        surface_visible=True,
    )
    return {
        "id": f"{message_id}:artifact:seed:{index}",
        "kind": "artifact",
        "artifact": {
            "id": f"seed-phone-file-preview-{index}",
            "artifactId": f"seed-phone-file-preview-{index}",
            "kind": "file",
            "title": absolute_path.name,
            "displayLabel": absolute_path.name,
            "mimeType": mime_type,
            "workspacePath": relative_path,
            "workspaceRelativePath": relative_path,
            "resourceRef": resource_ref,
            "source": "seed_phone_file_preview_session",
        },
        "timestamp": int(time.time() * 1000),
        "agentName": "智能主管",
        "agentAvatar": "supervisor",
        "agentRoleLabel": "主理人",
    }


def main() -> None:
    workspace_root = Path(workspace_resolution_service.get_main_workspace_path()).expanduser().resolve(strict=False)
    sample_paths = ["pdf.pdf", "PPT.pptx", "glb.glb"]
    missing = [item for item in sample_paths if not (workspace_root / item).is_file()]
    if missing:
        raise SystemExit(f"主工作区缺少验证文件：{', '.join(missing)}\nworkspace={workspace_root}")

    session_id = f"seed_phone_file_preview_{uuid.uuid4().hex[:12]}"
    run_id = f"run_seed_{uuid.uuid4().hex[:12]}"
    user_message_id = f"msg_user_{uuid.uuid4().hex[:12]}"
    assistant_message_id = f"msg_assistant_{uuid.uuid4().hex[:12]}"
    timestamp = int(time.time() * 1000)
    builder = CanonicalTranscriptBuilder()

    db.create_or_update_session(
        session_id,
        title="Phone 文件预览验证",
        user_id="anonymous",
        agent_id="supervisor",
        metadata={
            "source": "seed_phone_file_preview_session",
            "workspaceRoot": str(workspace_root),
        },
    )
    db.create_run_record(
        run_id,
        session_id=session_id,
        conversation_id=session_id,
        user_id="anonymous",
        run_type="chat",
        status="completed",
        trigger_source="seed_phone_file_preview_session",
        agent_id="supervisor",
        metadata={
            "source": "seed_phone_file_preview_session",
            "workspaceRoot": str(workspace_root),
        },
    )

    builder.create_message(
        message_id=user_message_id,
        session_id=session_id,
        run_id=run_id,
        ordinal=db.get_next_chat_canonical_ordinal(session_id),
        role="user",
        state="completed",
        metadata={"timestamp": timestamp, "clientMessageId": user_message_id},
        nodes=[
            {
                "id": f"{user_message_id}:narrative:seed",
                "kind": "narrative",
                "content": "请验证 Phone 端 PDF / PPT / GLB / Mermaid 文件预览链。",
                "timestamp": timestamp,
            }
        ],
    )

    link_lines = []
    for relative_path in sample_paths:
        resource_ref = build_workspace_resource_ref(
            workspace_relative_path=relative_path,
            path_plane="workspace_artifact",
            workspace_root=workspace_root,
            mime_type=_mime_type(workspace_root / relative_path),
            display_label=relative_path,
            previewable=True,
            downloadable=True,
            surface_visible=True,
        )
        link_lines.append(f"- [{relative_path}]({resource_ref['adminPath']})")

    mermaid = """```mermaid
flowchart TD
  A[Phone 历史会话] --> B[file-generic]
  B --> C{viewerKind}
  C -->|pdf| D[PDF 下载/公网预览]
  C -->|ppt| E[PPT 下载/公网预览]
  C -->|model| F[3D 预览 + 下载]
```"""
    content = "\n".join(
        [
            "这是 Phone 文件预览验证会话，包含 artifact 节点和正文链接两条链：",
            "",
            *link_lines,
            "",
            mermaid,
        ]
    )

    nodes = [
        {
            "id": f"{assistant_message_id}:narrative:seed",
            "kind": "narrative",
            "content": content,
            "timestamp": timestamp,
            "agentName": "智能主管",
            "agentAvatar": "supervisor",
            "agentRoleLabel": "主理人",
        }
    ]
    nodes.extend(_artifact_node(assistant_message_id, workspace_root, item, index) for index, item in enumerate(sample_paths, start=1))

    builder.create_message(
        message_id=assistant_message_id,
        session_id=session_id,
        run_id=run_id,
        ordinal=db.get_next_chat_canonical_ordinal(session_id),
        role="assistant",
        state="completed",
        metadata={
            "timestamp": timestamp,
            "agentId": "supervisor",
            "agentName": "智能主管",
            "agentAvatar": "supervisor",
            "agentRoleLabel": "主理人",
            "source": "seed_phone_file_preview_session",
        },
        nodes=nodes,
    )

    print(f"seedSessionId={session_id}")
    print(f"workspaceRoot={workspace_root}")


if __name__ == "__main__":
    main()
