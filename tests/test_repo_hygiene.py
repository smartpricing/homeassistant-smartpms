"""Static checks on the repository: manifest/HACS metadata, source and CI.

The repository is public and HACS installs straight from it, so the GitHub
workflows are part of the supply chain of every installation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "smartpms"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(workflow: dict):
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, step


# ---- manifest / HACS ------------------------------------------------------


def test_manifest_has_the_keys_hacs_and_hassfest_need() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == "smartpms"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["documentation"].startswith("https://github.com/smartpricing/")
    assert manifest["issue_tracker"].startswith("https://github.com/smartpricing/")


def test_manifest_declares_no_third_party_requirements() -> None:
    """No runtime dependency is installed into users' Home Assistant.

    Adding one widens the supply chain of every install: it must come with an
    exact pin and a Snyk scan (see the SPCWR-141 report)."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["requirements"] == []


def test_hacs_metadata() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == "SmartPMS"
    assert "homeassistant" in hacs


# ---- source -----------------------------------------------------------------


def test_source_never_disables_tls_verification() -> None:
    pattern = re.compile(
        r"ssl\s*=\s*False|verify_ssl\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False"
    )
    offenders = [
        f"{p.name}:{n}"
        for p in COMPONENT.glob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_source_uses_only_https_urls() -> None:
    urls = re.findall(
        r"https?://[^\s\"']+",
        "\n".join(p.read_text(encoding="utf-8") for p in COMPONENT.glob("*.py")),
    )
    assert urls, "expected the API base URL in const.py"
    assert all(u.startswith("https://") for u in urls)


# ---- GitHub workflows (supply chain) --------------------------------------


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_has_no_dangerous_triggers(path: Path) -> None:
    """No pull_request_target / workflow_run (fork code with a write token)."""
    triggers = _load(path)["on"]
    names = (
        set(triggers)
        if isinstance(triggers, dict)
        else set([triggers] if isinstance(triggers, str) else triggers)
    )
    assert not names & {"pull_request_target", "workflow_run"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_run_steps_do_not_interpolate_event_data(path: Path) -> None:
    """No ${{ github.event.* }} / head_ref inside shell scripts (injection)."""
    for _job, step in _steps(_load(path)):
        script = step.get("run") or ""
        assert "github.event." not in script
        assert "github.head_ref" not in script


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_uses_no_repository_secrets(path: Path) -> None:
    assert "secrets." not in path.read_text(encoding="utf-8")


def test_all_actions_are_pinned_to_a_commit_sha() -> None:
    unpinned = [
        f"{path.name}:{job}:{step['uses']}"
        for path in WORKFLOWS
        for job, step in _steps(_load(path))
        if "uses" in step
        and not step["uses"].startswith(("./", "docker://"))
        and not SHA_PIN.match(step["uses"])
    ]
    assert unpinned == []


def test_all_workflows_declare_read_only_token_permissions() -> None:
    offenders = []
    for path in WORKFLOWS:
        workflow = _load(path)
        perms = workflow.get("permissions")
        if perms is None or perms in ("write-all", "read-all"):
            offenders.append(f"{path.name}: permissions={perms!r}")
            continue
        grants = [perms] if isinstance(perms, str) else list(perms.values())
        grants += [
            v
            for job in (workflow.get("jobs") or {}).values()
            for v in (job.get("permissions") or {}).values()
        ]
        if "write" in grants:
            offenders.append(f"{path.name}: write permission")
    assert offenders == []


def test_checkout_does_not_persist_the_token() -> None:
    offenders = [
        f"{path.name}:{job}"
        for path in WORKFLOWS
        for job, step in _steps(_load(path))
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and (step.get("with") or {}).get("persist-credentials") is not False
    ]
    assert offenders == []


def test_dependabot_keeps_pinned_actions_current() -> None:
    """SHA pins only stay safe if they are bumped: Dependabot watches them."""
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert "github-actions" in ecosystems
