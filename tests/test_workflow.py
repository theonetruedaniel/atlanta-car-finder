from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_daily_workflow_has_required_budget_and_overlap_controls():
    workflow = yaml.safe_load(
        (ROOT / "docs/daily-inventory.example.yml").read_text()
    )

    assert "workflow_dispatch" in workflow["on"]
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["on"]["pull_request"] == {"branches": ["main"]}
    assert workflow["on"]["schedule"] == [{"cron": "17 10 * * *"}]
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    assert workflow["concurrency"] == {
        "group": "daily-inventory-crawl",
        "cancel-in-progress": False,
    }
    job = workflow["jobs"]["crawl-and-upload"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 40


def test_daily_workflow_tests_before_crawl_and_uses_short_lived_oidc_auth():
    workflow_text = (ROOT / "docs/daily-inventory.example.yml").read_text()
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["crawl-and-upload"]
    steps = job["steps"]
    commands = [step.get("run", "") for step in steps]
    test_index = next(
        index for index, command in enumerate(commands) if "python -m pytest" in command
    )
    crawl_index = next(
        index for index, command in enumerate(commands) if "crawl all" in command
    )

    assert test_index < crawl_index
    assert any("scrapling install" in command for command in commands)
    token_step = next(step for step in steps if step.get("id") == "dashboard-token")
    assert token_step["uses"] == "actions/github-script@v7"
    assert 'core.getIDToken("atlanta-car-finder-ingestion")' in token_step["with"][
        "script"
    ]
    assert "core.setSecret(token)" in token_step["with"]["script"]
    assert steps[crawl_index]["env"] == {
        "DASHBOARD_INGESTION_URL": "https://dashboard.example.com/api/ingestion/scrapling",
        "SCRAPLING_INGESTION_SECRET": "${{ steps.dashboard-token.outputs.token }}",
    }
    assert "env" not in job
    assert "${{ secrets." not in workflow_text

