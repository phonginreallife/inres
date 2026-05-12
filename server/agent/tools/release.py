"""
InRes Release Management Tools (Anthropic tool format + async handlers).

**Design:** Jira, Confluence, GitHub, and ArgoCD are **not** implemented here. Admins attach
those products via user-configured MCP servers (Integrations). The model should call those
MCP tools for vendor APIs.

This module only provides:
- InRes Go API workflow state (create/update/approve/status)
- Local git clone / branch / YAML edits / SOPS command hints
- Git commit + push in the release workspace
- Recording PR URL/number on the release after the GitHub MCP creates the PR
"""

import json
import logging
import os
import re
import shutil
import subprocess
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from tools.inres_api import get_inres_api_base_url

logger = logging.getLogger(__name__)

# Paths / defaults (override via env)
RELEASE_WORKSPACE_DIR = os.getenv("RELEASE_WORKSPACE_DIR", "/tmp/release-workspaces")
INFRA_REPO = os.getenv(
    "INFRA_REPO", "opswat-eng/mdaas-infrastructure-template-prod"
)
INFRA_REPO_BASE_BRANCH = os.getenv("INFRA_REPO_BASE_BRANCH", "master")

# Dynamic auth context (set per WebSocket session)
_auth_token_ctx: ContextVar[Optional[str]] = ContextVar("release_auth_token", default=None)
_org_id_ctx: ContextVar[Optional[str]] = ContextVar("release_org_id", default=None)
_project_id_ctx: ContextVar[Optional[str]] = ContextVar("release_project_id", default=None)


def set_auth_token(token: str) -> None:
    _auth_token_ctx.set(token)


def get_auth_token() -> str:
    return _auth_token_ctx.get() or os.getenv("inres_API_KEY", "")


def set_org_id(org_id: str) -> None:
    _org_id_ctx.set(org_id)


def get_org_id() -> str:
    return _org_id_ctx.get() or ""


def set_project_id(project_id: str) -> None:
    _project_id_ctx.set(project_id)


def get_project_id() -> str:
    return _project_id_ctx.get() or ""


def _api_headers() -> dict:
    headers = {
        "Authorization": f"Bearer {get_auth_token()}",
        "Content-Type": "application/json",
    }
    org_id = get_org_id()
    if org_id:
        headers["X-Org-ID"] = org_id
    project_id = get_project_id()
    if project_id:
        headers["X-Project-ID"] = project_id
    return headers


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ─── How to use external MCPs (admin-configured) ─────────────────────────────


_INTEGRATION_GUIDE = """
## Release workflow + external MCPs

**Jira & Confluence** — Use the **MCP servers your admin configured** in InRes (Integrations),
e.g. Atlassian Remote MCP, official Jira/Confluence servers, or equivalents. Tool names differ
by server (`jira_*`, `confluence_*`, `mcp_atlassian_*`, etc.): discover them in the tool list,
fetch the release ticket and any linked Confluence guidelines **before** calling
`release_create_workflow`.

**GitHub** — Use your project's **GitHub MCP** to open or inspect PRs. After editing files in
the cloned repo, call `release_commit_and_push`, create the PR with GitHub MCP, then call
`release_record_pr` with the PR URL (and number if known) so the InRes release record stays
linked.

**ArgoCD** — Use your project's **ArgoCD MCP** to sync apps and read health during deploy steps.
Do not rely on static agent env vars for ArgoCD; credentials live in the MCP connection.

**These `release_*` tools** — InRes API + local git/YAML/SOPS helpers only; they do not call
Jira/Confluence/GitHub/ArgoCD HTTP APIs.
""".strip()

_INTEGRATION_SECTIONS = {
    "jira": "Use your configured **Jira** MCP tools to load the release ticket (fields, links, description).",
    "confluence": "Use your configured **Confluence** MCP tools to read guideline pages (from Jira links or URLs).",
    "github": "Use your configured **GitHub** MCP for PR create/view/check. After `release_commit_and_push`, open the PR via MCP then `release_record_pr`.",
    "argocd": "Use your configured **ArgoCD** MCP to sync applications and read health for deploy steps.",
    "workflow": "Use `release_create_workflow`, `release_update_step`, `release_request_approval`, and `release_get_status` for InRes state only.",
}


async def _release_integration_guide_impl(args: dict[str, Any]) -> dict[str, Any]:
    focus = (args.get("focus") or "all").strip().lower()
    if focus == "all" or focus not in _INTEGRATION_SECTIONS:
        return _ok(_INTEGRATION_GUIDE)
    section = _INTEGRATION_SECTIONS[focus]
    return _ok(f"{section}\n\n{_INTEGRATION_GUIDE}")


# ─── Workflow state (InRes API) ───────────────────────────────────────────────


async def _release_create_workflow_impl(args: dict[str, Any]) -> dict[str, Any]:
    jira_ticket_id = args.get("jira_ticket_id", "").strip()
    version = args.get("version", "").strip()
    region = args.get("region", "").strip()
    confluence_page_url = args.get("confluence_page_url", "")

    if not jira_ticket_id or not version or not region:
        return _err("Error: jira_ticket_id, version, and region are required")

    payload: dict[str, Any] = {
        "jira_ticket_id": jira_ticket_id,
        "version": version,
        "region": region,
    }
    if isinstance(confluence_page_url, str) and confluence_page_url.strip():
        payload["confluence_page_url"] = confluence_page_url.strip()
    org = (get_org_id() or "").strip()
    if org:
        payload["organization_id"] = org
    pid = (get_project_id() or "").strip()
    if pid:
        payload["project_id"] = pid

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{get_inres_api_base_url()}/releases",
                json=payload,
                headers=_api_headers(),
            ) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    release_id = data.get("id", "")
                    steps = data.get("steps", [])
                    step_summary = "\n".join(
                        f"  - {s['step_type']}: {s['status']}" for s in steps
                    )
                    return _ok(
                        f"Release workflow created successfully.\n"
                        f"Release ID: {release_id}\n"
                        f"Version: {version}\n"
                        f"Region: {region}\n"
                        f"Jira: {jira_ticket_id}\n"
                        f"Steps:\n{step_summary}"
                    )
                else:
                    body = await response.text()
                    return _err(
                        f"Error: Failed to create release workflow (status {response.status}): {body}"
                    )
    except Exception as e:
        return _err(f"Error creating release workflow: {e}")


async def _release_update_step_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    step_type = args.get("step_type", "").strip()
    status = args.get("status", "").strip()

    if not release_id or not step_type or not status:
        return _err("Error: release_id, step_type, and status are required")

    payload: dict[str, Any] = {"status": status}
    output = args.get("output")
    if output:
        payload["output"] = output if isinstance(output, dict) else {"data": output}
    error_message = args.get("error_message", "")
    if error_message:
        payload["error_message"] = error_message

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{get_inres_api_base_url()}/releases/{release_id}/steps/{step_type}",
                json=payload,
                headers=_api_headers(),
            ) as response:
                if response.status == 200:
                    return _ok(
                        f"Step '{step_type}' updated to '{status}' for release {release_id}"
                    )
                else:
                    body = await response.text()
                    return _err(f"Error updating step: {body}")
    except Exception as e:
        return _err(f"Error updating step: {e}")


async def _release_request_approval_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    step_type = args.get("step_type", "").strip()
    message = args.get("message", "Approval requested")

    if not release_id or not step_type:
        return _err("Error: release_id and step_type are required")

    payload = {"status": "awaiting_approval"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{get_inres_api_base_url()}/releases/{release_id}/steps/{step_type}",
                json=payload,
                headers=_api_headers(),
            ) as response:
                if response.status == 200:
                    return _ok(
                        f"Approval requested for step '{step_type}'.\n"
                        f"Message: {message}\n"
                        f"The user can approve or reject this step via the release dashboard "
                        f"or by responding in this conversation."
                    )
                else:
                    body = await response.text()
                    return _err(f"Error requesting approval: {body}")
    except Exception as e:
        return _err(f"Error requesting approval: {e}")


async def _release_get_status_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    if not release_id:
        return _err("Error: release_id is required")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{get_inres_api_base_url()}/releases/{release_id}/status",
                headers=_api_headers(),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    steps = data.get("steps", [])
                    step_lines = "\n".join(
                        f"  - {s['step_type']}: {s['status']}" for s in steps
                    )
                    return _ok(
                        f"Release {data.get('id', release_id)}\n"
                        f"Status: {data.get('status', 'unknown')}\n"
                        f"Version: {data.get('version', '')}\n"
                        f"Region: {data.get('region', '')}\n"
                        f"PR: {data.get('pr_url', 'N/A')}\n"
                        f"Steps:\n{step_lines}"
                    )
                else:
                    body = await response.text()
                    return _err(f"Error getting release status: {body}")
    except Exception as e:
        return _err(f"Error getting release status: {e}")


# ─── Git / YAML operations (local workspace) ─────────────────────────────────


def _get_workspace(release_id: str) -> str:
    ws = os.path.join(RELEASE_WORKSPACE_DIR, release_id)
    os.makedirs(ws, exist_ok=True)
    return ws


async def _release_clone_and_branch_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    branch_name = args.get("branch_name", "").strip()
    repo = args.get("repo", INFRA_REPO).strip()

    if not release_id or not branch_name:
        return _err("Error: release_id and branch_name are required")

    workspace = _get_workspace(release_id)
    repo_dir = os.path.join(workspace, "repo")

    try:
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

        repo_url = f"git@github.com:{repo}.git"

        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "-b", INFRA_REPO_BASE_BRANCH, repo_url, repo_dir],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return _err(f"Error cloning repo: {proc.stderr}")

        proc = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        if proc.returncode != 0:
            return _err(f"Error creating branch: {proc.stderr}")

        return _ok(
            f"Repository cloned and branch '{branch_name}' created.\n"
            f"Workspace: {repo_dir}\n"
            f"Base: {INFRA_REPO_BASE_BRANCH}"
        )
    except subprocess.TimeoutExpired:
        return _err("Error: Git clone timed out after 120 seconds")
    except Exception as e:
        return _err(f"Error in clone/branch: {e}")


async def _release_apply_yaml_changes_impl(args: dict[str, Any]) -> dict[str, Any]:
    """Apply image tag changes to ArgoCD Application YAML files."""
    release_id = args.get("release_id", "").strip()
    changes_raw = args.get("changes")

    if not release_id:
        return _err("Error: release_id is required")
    if not changes_raw:
        return _err("Error: changes list is required")

    if isinstance(changes_raw, str):
        try:
            changes = json.loads(changes_raw)
        except json.JSONDecodeError as e:
            return _err(f"Error: Invalid JSON in changes: {e}")
    else:
        changes = changes_raw

    if not isinstance(changes, list) or len(changes) == 0:
        return _err("Error: changes must be a non-empty list")

    workspace = _get_workspace(release_id)
    repo_dir = os.path.join(workspace, "repo")

    if not os.path.isdir(repo_dir):
        return _err(
            "Error: Repository not cloned. Call release_clone_and_branch first."
        )

    results = []
    errors = []

    for change in changes:
        file_path = change.get("file", "")
        old_tag = change.get("old_tag", "")
        new_tag = change.get("new_tag", "")

        if not file_path or not old_tag or not new_tag:
            errors.append(f"Skipped invalid change entry: {change}")
            continue

        full_path = os.path.join(repo_dir, file_path)
        if not os.path.isfile(full_path):
            errors.append(f"File not found: {file_path}")
            continue

        try:
            with open(full_path, "r") as f:
                content = f.read()

            count = content.count(old_tag)
            if count == 0:
                errors.append(f"Tag '{old_tag}' not found in {file_path}")
                continue

            new_content = content.replace(old_tag, new_tag)
            with open(full_path, "w") as f:
                f.write(new_content)

            results.append(
                f"{file_path}: replaced '{old_tag}' -> '{new_tag}' ({count} occurrence(s))"
            )

        except Exception as e:
            errors.append(f"Error processing {file_path}: {e}")

    summary = f"Applied {len(results)} change(s):\n"
    summary += "\n".join(f"  + {r}" for r in results)
    if errors:
        summary += f"\n\n{len(errors)} error(s):\n"
        summary += "\n".join(f"  ! {e}" for e in errors)

    return _ok(summary)


async def _release_list_yaml_files_impl(args: dict[str, Any]) -> dict[str, Any]:
    """List YAML files in a region's application directory."""
    release_id = args.get("release_id", "").strip()
    region = args.get("region", "").strip()

    if not release_id or not region:
        return _err("Error: release_id and region are required")

    workspace = _get_workspace(release_id)
    repo_dir = os.path.join(workspace, "repo")

    if not os.path.isdir(repo_dir):
        return _err("Error: Repository not cloned. Call release_clone_and_branch first.")

    region_dir_candidates = [
        f"product-application/production/mdaas-prod/mdaas-{region}-application",
        f"product-application/production/mdaas-prod/mdaas-{region.replace('-', '')}-application",
    ]

    found_dir = None
    for candidate in region_dir_candidates:
        full = os.path.join(repo_dir, candidate)
        if os.path.isdir(full):
            found_dir = full
            break

    if not found_dir:
        prod_dir = os.path.join(repo_dir, "product-application/production/mdaas-prod")
        if os.path.isdir(prod_dir):
            dirs = [
                d
                for d in os.listdir(prod_dir)
                if region.replace("-", "") in d.replace("-", "")
            ]
            if dirs:
                found_dir = os.path.join(prod_dir, dirs[0])

    if not found_dir:
        return _err(f"Error: Could not find application directory for region '{region}'")

    files = []
    for f in sorted(os.listdir(found_dir)):
        if f.endswith((".yaml", ".yml")):
            full_path = os.path.join(found_dir, f)
            rel_path = os.path.relpath(full_path, repo_dir)

            with open(full_path, "r") as fh:
                content = fh.read()

            tags = re.findall(r"tag:\s*(.+)", content)
            linux_images = re.findall(r"linux\.image:\s*(.+)", content)
            windows_images = re.findall(r"windows\.image:\s*(.+)", content)

            file_info = f"  {rel_path}"
            if tags:
                file_info += f"\n    tags: {', '.join(t.strip() for t in tags)}"
            if linux_images:
                file_info += f"\n    linux: {', '.join(i.strip() for i in linux_images)}"
            if windows_images:
                file_info += f"\n    windows: {', '.join(i.strip() for i in windows_images)}"

            files.append(file_info)

    return _ok(
        f"Region directory: {os.path.relpath(found_dir, repo_dir)}\n"
        f"Found {len(files)} YAML file(s):\n\n" + "\n".join(files)
    )


# ─── SOPS command hints ───────────────────────────────────────────────────────


async def _release_generate_sops_commands_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    region = args.get("region", "").strip()
    secret_changes_raw = args.get("secret_changes")

    if not release_id or not region:
        return _err("Error: release_id and region are required")

    workspace = _get_workspace(release_id)
    repo_dir = os.path.join(workspace, "repo")

    if not os.path.isdir(repo_dir):
        return _err("Error: Repository not cloned. Call release_clone_and_branch first.")

    secret_dir_candidates = [
        f"product-application/secret-center/mdaas-prod/mdaas-{region}",
        f"product-application/secret-center/mdaas-prod/mdaas-{region.replace('-', '')}",
    ]

    found_dir = None
    for candidate in secret_dir_candidates:
        full = os.path.join(repo_dir, candidate)
        if os.path.isdir(full):
            found_dir = full
            break

    if not found_dir:
        sc_root = os.path.join(repo_dir, "product-application/secret-center/mdaas-prod")
        if os.path.isdir(sc_root):
            dirs = [
                d
                for d in os.listdir(sc_root)
                if region.replace("-", "") in d.replace("-", "")
            ]
            if dirs:
                found_dir = os.path.join(sc_root, dirs[0])

    if not found_dir:
        return _err(f"Error: Could not find secret-center directory for region '{region}'")

    kms_arn = ""
    for root, _, files in os.walk(found_dir):
        for name in files:
            if name.endswith((".yaml", ".yml")) and "sops" in name.lower():
                try:
                    with open(os.path.join(root, name), "r") as fh:
                        for line in fh:
                            if "arn:aws:kms" in line:
                                m = re.search(r"arn:aws:kms:[^\s'\"]+", line)
                                if m:
                                    kms_arn = m.group(0)
                                    break
                except OSError:
                    pass
        if kms_arn:
            break

    secret_notes = ""
    if secret_changes_raw:
        if isinstance(secret_changes_raw, str):
            secret_notes = secret_changes_raw.strip()
        else:
            secret_notes = json.dumps(secret_changes_raw, indent=2)

    commands = (
        f"SOPS workflow for region '{region}' (secrets directory: "
        f"{os.path.relpath(found_dir, repo_dir)})\n\n"
    )
    if secret_notes:
        commands += f"Planned secret changes:\n{secret_notes}\n\n"

    commands += (
        f"1. cd to repo:\n   cd {repo_dir}\n\n"
        f"2. For each encrypted file under the region directory, run:\n"
    )

    yaml_files = [
        os.path.join(found_dir, f)
        for f in sorted(os.listdir(found_dir))
        if f.endswith((".yaml", ".yml"))
    ]
    if not yaml_files:
        commands += "   (No YAML files found — adjust paths manually.)\n"
    for ef in yaml_files[:12]:
        rel = os.path.relpath(ef, repo_dir)
        commands += (
            f"\n   File: {rel}\n"
            f"   Decrypt:\n"
            f"   sops -d {rel} > {rel}.dec.yaml\n\n"
            f"   Edit the decrypted file with the required changes:\n"
            f"   vi {rel}.dec.yaml\n\n"
            f"   Re-encrypt:\n"
            f"   sops -e"
        )
        if kms_arn:
            commands += f" --kms '{kms_arn}'"
        commands += (
            f" --encrypted-regex '^(data|stringData)$'"
            f" {rel}.dec.yaml > {rel}\n\n"
            f"   Clean up:\n"
            f"   rm {rel}.dec.yaml\n\n"
        )

    commands += (
        f"3. Verify the changes:\n"
        f"   git diff {os.path.relpath(found_dir, repo_dir)}/\n\n"
        f"After completing these steps, use `release_commit_and_push`, then your GitHub MCP "
        f"for the PR, then `release_record_pr`."
    )

    return _ok(commands)


# ─── Git commit / push + link PR on InRes ─────────────────────────────────────


async def _release_commit_and_push_impl(args: dict[str, Any]) -> dict[str, Any]:
    """Stage, commit, and push; PR is created separately via GitHub MCP."""
    release_id = args.get("release_id", "").strip()
    title = args.get("commit_message", "").strip() or args.get("title", "").strip()

    if not release_id or not title:
        return _err("Error: release_id and commit_message (or title) are required")

    workspace = _get_workspace(release_id)
    repo_dir = os.path.join(workspace, "repo")

    if not os.path.isdir(repo_dir):
        return _err("Error: Repository not cloned. Call release_clone_and_branch first.")

    try:
        proc = subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        if proc.returncode != 0:
            return _err(f"Error staging changes: {proc.stderr}")

        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        if not proc.stdout.strip():
            return _err("Error: No changes to commit")

        proc = subprocess.run(
            ["git", "commit", "-m", title],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        if proc.returncode != 0:
            return _err(f"Error committing: {proc.stderr}")

        proc = subprocess.run(
            ["git", "push", "-u", "origin", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=120,
        )
        if proc.returncode != 0:
            return _err(f"Error pushing: {proc.stderr}")

        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        branch = proc.stdout.strip() if proc.returncode == 0 else "(unknown)"

        msg = (
            f"Pushed branch `{branch}` for release workspace `{release_id}`.\n"
            f"Repository: `{INFRA_REPO}` (base branch for PRs is usually `{INFRA_REPO_BASE_BRANCH}`).\n\n"
            f"Next: use your **GitHub MCP** to open a pull request from `{branch}` into "
            f"`{INFRA_REPO_BASE_BRANCH}`, then call **release_record_pr** with the PR URL "
            f"(and PR number if available) so InRes links the release to the PR."
        )
        return _ok(msg)
    except subprocess.TimeoutExpired:
        return _err("Error: Git push timed out")
    except Exception as e:
        return _err(f"Error during commit/push: {e}")


async def _release_record_pr_impl(args: dict[str, Any]) -> dict[str, Any]:
    release_id = args.get("release_id", "").strip()
    pr_url = (args.get("pr_url") or "").strip()
    pr_number_raw = args.get("pr_number")

    if not release_id or not pr_url:
        return _err("Error: release_id and pr_url are required")

    pr_number: Optional[int] = None
    if pr_number_raw is not None and str(pr_number_raw).strip() != "":
        try:
            pr_number = int(pr_number_raw)
        except (TypeError, ValueError):
            return _err("Error: pr_number must be an integer when provided")

    if pr_number is None:
        m = re.search(r"/pull/(\d+)", pr_url)
        if m:
            pr_number = int(m.group(1))

    payload: dict[str, Any] = {"pr_url": pr_url}
    if pr_number is not None:
        payload["pr_number"] = pr_number

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{get_inres_api_base_url()}/releases/{release_id}",
                json=payload,
                headers=_api_headers(),
            ) as response:
                if response.status == 200:
                    extra = f", PR #{pr_number}" if pr_number is not None else ""
                    return _ok(
                        f"Release {release_id} updated with pr_url={pr_url}{extra}."
                    )
                body = await response.text()
                return _err(f"Error updating release (status {response.status}): {body}")
    except Exception as e:
        return _err(f"Error recording PR: {e}")


RELEASE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "release_integration_guide",
        "description": (
            "Explains how release automation splits between these InRes tools and "
            "admin-configured MCP servers (Jira, Confluence, GitHub, ArgoCD). Call early when "
            "planning a release. Optional focus: jira | confluence | github | argocd | "
            "workflow | all (default all)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "Optional section focus",
                },
            },
        },
    },
    {
        "name": "release_create_workflow",
        "description": (
            "Create a new release workflow in InRes after you have gathered ticket and guideline "
            "details using the project's Jira/Confluence MCP tools (not this server)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jira_ticket_id": {"type": "string"},
                "version": {"type": "string"},
                "region": {"type": "string"},
                "confluence_page_url": {"type": "string"},
            },
            "required": ["jira_ticket_id", "version", "region"],
        },
    },
    {
        "name": "release_update_step",
        "description": (
            "Update the status and output of a release workflow step. Use to mark steps as "
            "in_progress, completed, failed, or skipped."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "step_type": {"type": "string"},
                "status": {"type": "string"},
                "output": {"description": "Optional structured output", "type": "string"},
                "error_message": {"type": "string"},
            },
            "required": ["release_id", "step_type", "status"],
        },
    },
    {
        "name": "release_request_approval",
        "description": (
            "Request human approval for a release workflow step. Marks the step as "
            "awaiting_approval and sends a notification."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "step_type": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["release_id", "step_type"],
        },
    },
    {
        "name": "release_get_status",
        "description": "Get the current status of a release workflow, including all step statuses.",
        "input_schema": {
            "type": "object",
            "properties": {"release_id": {"type": "string"}},
            "required": ["release_id"],
        },
    },
    {
        "name": "release_clone_and_branch",
        "description": (
            "Clone the infrastructure repository and create a feature branch for the release. "
            "Branch naming convention: ACO-XXXX-deploy-X.X.X-region"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "branch_name": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["release_id", "branch_name"],
        },
    },
    {
        "name": "release_list_yaml_files",
        "description": (
            "List all ArgoCD Application YAML files for a specific region, showing current "
            "image tags."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["release_id", "region"],
        },
    },
    {
        "name": "release_apply_yaml_changes",
        "description": (
            "Apply image tag changes to ArgoCD Application YAML files in the cloned repo. "
            "Provide a JSON list of {file, old_tag, new_tag} objects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "changes": {
                    "description": "JSON array or string of change objects",
                    "type": "string",
                },
            },
            "required": ["release_id", "changes"],
        },
    },
    {
        "name": "release_generate_sops_commands",
        "description": (
            "Generate step-by-step SOPS decrypt/edit/encrypt commands for updating secrets. "
            "The user must run these commands manually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "region": {"type": "string"},
                "secret_changes": {"type": "string"},
            },
            "required": ["release_id", "region"],
        },
    },
    {
        "name": "release_commit_and_push",
        "description": (
            "Stage all changes in the release workspace repo, commit, and push to origin. "
            "Does not create a GitHub PR — use your configured GitHub MCP for that, then "
            "release_record_pr."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "commit_message": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["release_id"],
        },
    },
    {
        "name": "release_record_pr",
        "description": (
            "After creating a pull request with your GitHub MCP, store pr_url (and optional "
            "pr_number) on the InRes release record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "pr_url": {"type": "string"},
                "pr_number": {"description": "Optional PR number", "type": "string"},
            },
            "required": ["release_id", "pr_url"],
        },
    },
]

RELEASE_TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "release_integration_guide": _release_integration_guide_impl,
    "release_create_workflow": _release_create_workflow_impl,
    "release_update_step": _release_update_step_impl,
    "release_request_approval": _release_request_approval_impl,
    "release_get_status": _release_get_status_impl,
    "release_clone_and_branch": _release_clone_and_branch_impl,
    "release_list_yaml_files": _release_list_yaml_files_impl,
    "release_apply_yaml_changes": _release_apply_yaml_changes_impl,
    "release_generate_sops_commands": _release_generate_sops_commands_impl,
    "release_commit_and_push": _release_commit_and_push_impl,
    "release_record_pr": _release_record_pr_impl,
}


__all__ = [
    "RELEASE_TOOL_SCHEMAS",
    "RELEASE_TOOL_HANDLERS",
    "set_auth_token",
    "get_auth_token",
    "set_org_id",
    "get_org_id",
    "set_project_id",
    "get_project_id",
]
