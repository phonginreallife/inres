#!/usr/bin/env python3
"""
Simulate the full InRes *release_* tool workflow end-to-end (no real Go API or GitHub).

What it demonstrates
----------------------
1. **release_integration_guide** — how InRes tools relate to Jira/Confluence/GitHub/Argo MCPs.
2. **release_create_workflow** — POST /releases (mocked).
3. **release_get_status** — GET /releases/{id}/status (mocked).
4. **release_update_step** — PATCH step status (mocked).
5. **release_request_approval** — PATCH awaiting_approval (mocked).
6. **release_list_yaml_files** / **release_apply_yaml_changes** — real temp workspace + YAML.
7. **release_generate_sops_commands** — real SOPS hint paths under the same workspace.
8. **release_commit_and_push** — real local `git init` + commit; **push is mocked** (no origin).
9. **release_record_pr** — PATCH release with pr_url (mocked).

Run from the agent package root::

    cd server/agent
    python3 scripts/simulate_release_process.py

Environment (optional)
----------------------
- ``RELEASE_WORKSPACE_DIR`` — defaults to a new temp directory for this run.
- ``inres_API_URL`` — only used to build URLs for mocked responses (can stay default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest.mock
from pathlib import Path
from typing import Any, Callable, Dict, List

# Agent package on path
_AGENT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENT_ROOT))
os.chdir(_AGENT_ROOT)

SIM_RELEASE_ID = "11111111-1111-1111-1111-111111111111"
SIM_REGION = "us-west"
SIM_JIRA = "ACO-4242"
SIM_VERSION = "2.4.1"


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_tool_result(label: str, payload: Dict[str, Any], max_chars: int = 2000) -> None:
    err = payload.get("isError")
    blocks = payload.get("content") or []
    texts: List[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            texts.append(b.get("text", ""))
    body = "\n".join(texts).strip() or json.dumps(payload, indent=2, default=str)
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n... ({len(body)} chars total, truncated)"
    print(f"\n--- {label} {'[ERROR]' if err else '[ok]'} ---\n{body}\n")


class _FakeResponse:
    __slots__ = ("status", "_json", "_text")

    def __init__(self, status: int, json_data: Any = None, text_body: str = ""):
        self.status = status
        self._json = json_data
        self._text = text_body

    async def json(self) -> Any:
        return self._json if self._json is not None else {}

    async def text(self) -> str:
        return self._text


class _ReqCM:
    __slots__ = ("_resp",)

    def __init__(self, resp: _FakeResponse) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeResponse:
        return self._resp

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeClientSession:
    """Minimal aiohttp.ClientSession stand-in for tools.release."""

    def __init__(self, router: Callable[..., _ReqCM]):
        self._router = router

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> _ReqCM:
        return self._router("POST", url, kwargs)

    def get(self, url: str, **kwargs: Any) -> _ReqCM:
        return self._router("GET", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> _ReqCM:
        return self._router("PATCH", url, kwargs)


def _default_steps() -> List[Dict[str, str]]:
    return [
        {"step_type": "gather_context", "status": "pending"},
        {"step_type": "code_change", "status": "pending"},
        {"step_type": "deploy", "status": "pending"},
    ]


def build_api_router(base_url: str) -> Callable[..., _ReqCM]:
    base = base_url.rstrip("/")

    def router(method: str, url: str, kwargs: Dict[str, Any]) -> _ReqCM:
        u = url.rstrip("/")
        if method == "POST" and u == f"{base}/releases":
            body = {
                "id": SIM_RELEASE_ID,
                "steps": _default_steps(),
            }
            return _ReqCM(_FakeResponse(201, body))
        if method == "GET" and u == f"{base}/releases/{SIM_RELEASE_ID}/status":
            body = {
                "id": SIM_RELEASE_ID,
                "status": "in_progress",
                "version": SIM_VERSION,
                "region": SIM_REGION,
                "pr_url": "",
                "steps": _default_steps(),
            }
            return _ReqCM(_FakeResponse(200, body))
        if method == "PATCH" and u.startswith(f"{base}/releases/{SIM_RELEASE_ID}/steps/"):
            return _ReqCM(_FakeResponse(200, {"ok": True}))
        if method == "PATCH" and u == f"{base}/releases/{SIM_RELEASE_ID}":
            return _ReqCM(_FakeResponse(200, {"id": SIM_RELEASE_ID, "pr_url": "https://github.com/org/repo/pull/99"}))
        return _ReqCM(_FakeResponse(404, None, f"unexpected {method} {url}"))

    return router


def _patch_subprocess_run_for_simulated_push() -> Any:
    """After real git add/commit, make push + rev-parse succeed without a remote."""

    orig = subprocess.run

    def fake_run(cmd: List[str], **kwargs: Any):
        cwd = kwargs.get("cwd") or os.getcwd()
        if cmd[:3] == ["git", "push", "-u"]:
            m = unittest.mock.MagicMock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m
        return orig(cmd, **kwargs)

    return unittest.mock.patch("subprocess.run", side_effect=fake_run)


def seed_minimal_infra_repo(workspace_root: Path, release_id: str, region: str) -> Path:
    """
    Create a tiny tree that matches what release_list_yaml_files expects,
    without cloning the real infra repo.
    """
    repo = workspace_root / release_id / "repo"
    app_dir = (
        repo
        / "product-application"
        / "production"
        / "mdaas-prod"
        / f"mdaas-{region}-application"
    )
    app_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = app_dir / "application.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "apiVersion: argoproj.io/v1alpha1",
                "kind: Application",
                "metadata:",
                "  name: demo-app",
                "spec:",
                "  source:",
                "    helm:",
                "      values: |",
                "        image:",
                "          tag: v2.4.0",
                "        linux:",
                "          image: registry.example.com/linux:v2.4.0",
                "        windows:",
                "          image: registry.example.com/win:v2.4.0",
            ]
        ),
        encoding="utf-8",
    )
    secret_dir = (
        repo
        / "product-application"
        / "secret-center"
        / "mdaas-prod"
        / f"mdaas-{region}"
    )
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "values-sops.yaml").write_text(
        "\n".join(
            [
                "sops:",
                "  kms:",
                "    - arn: arn:aws:kms:us-west-2:123456789012:key/abcd-1234",
                "stringData: {}",
            ]
        ),
        encoding="utf-8",
    )
    # Git init so commit_and_push can run (push mocked)
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "sim@inres.local"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Release Sim"], cwd=str(repo), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore(sim): seed workspace for release simulation"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="inres-release-sim-")
    os.environ["RELEASE_WORKSPACE_DIR"] = tmp
    # Import after env so release module picks up workspace
    import tools.release as release_tools
    from tools import inres_api

    base_url = inres_api.get_inres_api_base_url()
    router = build_api_router(base_url)

    release_tools.set_auth_token("simulated-jwt")
    release_tools.set_org_id("00000000-0000-0000-0000-000000000001")
    release_tools.set_project_id("00000000-0000-0000-0000-000000000002")

    banner("Phase 0 — Narrative (what the agent is supposed to do)")
    print(
        """
Typical production flow (orchestrated by the LLM, not this script):

  1. Use **Jira / Confluence MCP** tools to read the ticket and linked runbooks.
  2. Call **release_integration_guide** once if the model needs the split of responsibilities.
  3. Call **release_create_workflow** so InRes persists workflow state.
  4. Optionally **release_clone_and_branch** against the real infra repo (SSH + network).
  5. **release_list_yaml_files** → **release_apply_yaml_changes** for image bumps.
  6. **release_generate_sops_commands** for operator-run SOPS (no KMS in the agent).
  7. **release_commit_and_push** then **GitHub MCP** for the PR, then **release_record_pr**.
  8. **ArgoCD MCP** for sync/health; **release_update_step** / **release_get_status** as gates.

This script runs the **InRes-side** tools with a mocked Go API and a synthetic repo tree.
""".strip()
    )

    H = release_tools.RELEASE_TOOL_HANDLERS

    banner("1) release_integration_guide (focus=workflow)")
    print_tool_result("guide", await H["release_integration_guide"]({"focus": "workflow"}))

    session_cm = unittest.mock.patch(
        "tools.release.aiohttp.ClientSession",
        side_effect=lambda: _FakeClientSession(router),
    )

    with session_cm:
        banner("2) release_create_workflow (mock POST /releases)")
        print_tool_result(
            "create",
            await H["release_create_workflow"](
                {
                    "jira_ticket_id": SIM_JIRA,
                    "version": SIM_VERSION,
                    "region": SIM_REGION,
                    "confluence_page_url": "https://wiki.example.com/runbooks/mdaas-release",
                }
            ),
        )

        banner("3) release_get_status (mock GET)")
        print_tool_result("status", await H["release_get_status"]({"release_id": SIM_RELEASE_ID}))

        banner("4) release_update_step (mock PATCH step)")
        print_tool_result(
            "update_step",
            await H["release_update_step"](
                {
                    "release_id": SIM_RELEASE_ID,
                    "step_type": "gather_context",
                    "status": "completed",
                    "output": {"source": "simulate_release_process.py"},
                }
            ),
        )

        banner("5) release_request_approval (mock PATCH → awaiting_approval)")
        print_tool_result(
            "request_approval",
            await H["release_request_approval"](
                {
                    "release_id": SIM_RELEASE_ID,
                    "step_type": "code_change",
                    "message": "Simulated: please approve YAML changes",
                }
            ),
        )

    # Local workspace (no HTTP): seed repo tree
    banner("6) Seed synthetic infra workspace (skip real git clone)")
    repo_path = seed_minimal_infra_repo(Path(tmp), SIM_RELEASE_ID, SIM_REGION)
    print(f"Workspace: {repo_path}")

    banner("7) release_list_yaml_files")
    print_tool_result(
        "list_yaml",
        await H["release_list_yaml_files"]({"release_id": SIM_RELEASE_ID, "region": SIM_REGION}),
    )

    banner("8) release_apply_yaml_changes (string replace on tag)")
    changes = json.dumps(
        [
            {
                "file": "product-application/production/mdaas-prod/mdaas-us-west-application/application.yaml",
                "old_tag": "v2.4.0",
                "new_tag": "v2.4.1",
            }
        ]
    )
    print_tool_result(
        "apply_yaml",
        await H["release_apply_yaml_changes"]({"release_id": SIM_RELEASE_ID, "changes": changes}),
    )

    banner("9) release_generate_sops_commands")
    print_tool_result(
        "sops_hints",
        await H["release_generate_sops_commands"](
            {
                "release_id": SIM_RELEASE_ID,
                "region": SIM_REGION,
                "secret_changes": "Rotate TLS cert reference for demo",
            }
        ),
        max_chars=4000,
    )

    banner("10) release_commit_and_push (commit real; push mocked)")
    with _patch_subprocess_run_for_simulated_push():
        print_tool_result(
            "commit_push",
            await H["release_commit_and_push"](
                {
                    "release_id": SIM_RELEASE_ID,
                    "commit_message": "chore(release): bump demo image to v2.4.1 (simulation)",
                }
            ),
        )

    with session_cm:
        banner("11) release_record_pr (mock PATCH /releases/{id})")
        print_tool_result(
            "record_pr",
            await H["release_record_pr"](
                {
                    "release_id": SIM_RELEASE_ID,
                    "pr_url": "https://github.com/opswat-eng/mdaas-infrastructure-template-prod/pull/4242",
                    "pr_number": "4242",
                }
            ),
        )

    banner("Done")
    print(
        f"""
Summary
-------
- Mock API base: {base_url}
- Workspace dir: {tmp}
- Synthetic release id: {SIM_RELEASE_ID}

Delete workspace when finished: rm -rf {tmp}
""".strip()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
