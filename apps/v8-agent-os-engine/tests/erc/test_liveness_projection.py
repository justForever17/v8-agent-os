from erc.liveness_projection import build_liveness_view


def test_interrupted_run_projects_terminal_liveness():
    view = build_liveness_view(
        run_record={"id": "run-interrupted", "status": "interrupted"},
        workflow_view={},
        runtime_events=[],
    )

    assert view["status"] == "terminal"
    assert view["runStatus"] == "interrupted"
