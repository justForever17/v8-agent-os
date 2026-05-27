from graph.supervisor_turn import _runtime_episode_handoff_ready, _runtime_handoff_final_message


def test_runtime_episode_handoff_ready_requires_resume_terminal_state():
    assert _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "wait_episode",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "episode_terminal",
                "handoffCount": 0,
            }
        }
    )


def test_runtime_handoff_final_message_blocks_post_handoff_tool_loop():
    message = _runtime_handoff_final_message()
    content = str(message.content)
    assert "Do not call tools" in content
    assert "produce one concise user-facing completion/status summary" in content
