"""
inres Incident Management Tools (Anthropic tool format + async handlers).

Tools for fetching and managing incidents from the inres backend API.
"""

import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from tools.inres_api import get_inres_api_base_url

# Configuration
# API_TOKEN_KEY should be set to Supabase Service Role key for system access
API_TOKEN_KEY = os.getenv("inres_API_KEY", "")

from contextvars import ContextVar

# Dynamic token storage (set per WebSocket session, async-safe)
_auth_token_ctx: ContextVar[Optional[str]] = ContextVar("auth_token", default=None)
_org_id_ctx: ContextVar[Optional[str]] = ContextVar("org_id", default=None)
_project_id_ctx: ContextVar[Optional[str]] = ContextVar("project_id", default=None)


def set_auth_token(token: str) -> None:
    """
    Set the authentication token to use for API requests.
    This should be called at the start of each WebSocket session.

    Args:
        token: The JWT authentication token from the frontend
    """
    _auth_token_ctx.set(token)

    print(f"Auth token set for incident_tools (length: {len(token) if token else 0})")


def get_auth_token() -> str:
    """
    Get the current authentication token.
    Prioritizes dynamic token over environment variable.

    Returns:
        The authentication token to use for API requests
    """
    return _auth_token_ctx.get() or API_TOKEN_KEY


def set_org_id(org_id: str) -> None:
    """
    Set the organization ID for tenant isolation.
    This should be called at the start of each WebSocket session.

    Args:
        org_id: The organization ID from the frontend context
    """
    _org_id_ctx.set(org_id)
    # print(f"Org ID set for incident_tools: {org_id}")


def get_org_id() -> str:
    """
    Get the current organization ID.

    Returns:
        The organization ID for tenant isolation
    """
    return _org_id_ctx.get() or ""


def set_project_id(project_id: str) -> None:
    """
    Set the project ID for optional filtering.
    This should be called at the start of each WebSocket session.

    Args:
        project_id: The project ID from the frontend context
    """
    _project_id_ctx.set(project_id)
    # print(f"Project ID set for incident_tools: {project_id}")


def get_project_id() -> str:
    """
    Get the current project ID.

    Returns:
        The project ID for optional filtering
    """
    return _project_id_ctx.get() or ""


# Implementation functions (callable directly)
async def _get_incidents_by_time_impl(args: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch incidents within a time range.

    Args:
        start_time: Start time in ISO 8601 format (e.g., "2024-01-01T00:00:00Z")
        end_time: End time in ISO 8601 format (e.g., "2024-01-01T23:59:59Z")
        status: Filter by status - "triggered", "acknowledged", "resolved", or "all" (default: "all")
        limit: Maximum number of incidents to return (default: 50, max: 1000)

    Returns:
        Dictionary with incident data or error information
    """
    start_time = args.get("start_time")
    end_time = args.get("end_time")
    status = args.get("status", "all")
    limit = args.get("limit", 50)

    # Validate inputs
    try:
        datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Invalid time format. Please use ISO 8601 format (e.g., '2024-01-01T00:00:00Z'). Error: {str(e)}",
                }
            ],
            "isError": True,
        }

    # Validate limit
    if limit < 1 or limit > 1000:
        return {
            "content": [
                {"type": "text", "text": "Error: Limit must be between 1 and 1000"}
            ],
            "isError": True,
        }

    # Build query parameters
    params = {"start_time": start_time, "end_time": end_time, "limit": limit}

    if status != "all":
        params["status"] = status

    # ReBAC: Add org_id for tenant isolation (MANDATORY) and project_id (OPTIONAL)
    # Priority: 1. Argument 2. Context 3. Environment Variable
    org_id = args.get("org_id") or get_org_id() or os.getenv("inres_ORG_ID")
    project_id = args.get("project_id") or get_project_id()
    if org_id:
        params["org_id"] = org_id
    if project_id:
        params["project_id"] = project_id

    # Make API request
    try:
        headers = {
            "Authorization": f"Bearer {get_auth_token()}",
            "Content-Type": "application/json",
        }
        # Also add org_id/project_id as headers for redundancy
        if org_id:
            headers["X-Org-ID"] = org_id
        if project_id:
            headers["X-Project-ID"] = project_id

        async with aiohttp.ClientSession() as session:
            url = f"{get_inres_api_base_url()}/incidents"

            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    incidents = data.get("incidents", [])

                    # Format the response
                    if not incidents:
                        result_text = (
                            f"No incidents found between {start_time} and {end_time}"
                        )
                        if status != "all":
                            result_text += f" with status '{status}'"
                    else:
                        result_text = f"Found {len(incidents)} incident(s) between {start_time} and {end_time}\n\n"

                        for idx, incident in enumerate(incidents, 1):
                            result_text += f"**Incident #{idx}**\n"
                            result_text += f"• ID: {incident.get('id', 'N/A')}\n"
                            result_text += (
                                f"•Title: {incident.get('title', 'N/A')}\n"
                            )
                            result_text += (
                                f"•Status: {incident.get('status', 'N/A')}\n"
                            )
                            result_text += (
                                f"•Severity: {incident.get('severity', 'N/A')}\n"
                            )
                            result_text += (
                                f"•Service: {incident.get('service_name', 'N/A')}\n"
                            )
                            result_text += (
                                f"•Created: {incident.get('created_at', 'N/A')}\n"
                            )
                            result_text += f"•Assigned to: {incident.get('assigned_to_name', 'Unassigned')}\n"

                            if incident.get("acknowledged_at"):
                                result_text += f"  • Acknowledged: {incident.get('acknowledged_at')}\n"
                            if incident.get("resolved_at"):
                                result_text += (
                                    f"  • Resolved: {incident.get('resolved_at')}\n"
                                )

                            result_text += "\n"

                    return {"content": [{"type": "text", "text": result_text}]}

                elif response.status == 401:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Authentication failed. Please check your API token.",
                            }
                        ],
                        "isError": True,
                    }

                else:
                    error_text = await response.text()
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: API request failed with status {response.status}\n{error_text}",
                            }
                        ],
                        "isError": True,
                    }

    except aiohttp.ClientError as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Network error occurred: {str(e)}\nPlease check if the inres API is running at {get_inres_api_base_url()}",
                }
            ],
            "isError": True,
        }

    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Unexpected error occurred: {str(e)}",
                }
            ],
            "isError": True,
        }


async def _get_incident_by_id_impl(args: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch detailed information about a specific incident.

    Args:
        incident_id: The unique identifier of the incident

    Returns:
        Dictionary with detailed incident data or error information
    """
    incident_id = args.get("incident_id")

    if not incident_id:
        return {
            "content": [{"type": "text", "text": "Error: incident_id is required"}],
            "isError": True,
        }

    try:
        headers = {
            "Authorization": f"Bearer {get_auth_token()}",
            "Content-Type": "application/json",
        }

        # ReBAC: Add org_id for tenant isolation (MANDATORY) and project_id (OPTIONAL)
        # Priority: 1. Argument 2. Context 3. Environment Variable
        org_id = args.get("org_id") or get_org_id() or os.getenv("inres_ORG_ID")
        project_id = args.get("project_id") or get_project_id()
        params = {}
        if org_id:
            params["org_id"] = org_id
            headers["X-Org-ID"] = org_id
        if project_id:
            params["project_id"] = project_id
            headers["X-Project-ID"] = project_id

        async with aiohttp.ClientSession() as session:
            url = f"{get_inres_api_base_url()}/incidents/{incident_id}"

            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    incident = await response.json()

                    # Format detailed response
                    result_text = f"🔍 **Incident Details**\n\n"
                    result_text += f"**Basic Information:**\n"
                    result_text += f"  • ID: {incident.get('id', 'N/A')}\n"
                    result_text += f"  • Title: {incident.get('title', 'N/A')}\n"
                    result_text += (
                        f"  • Description: {incident.get('description', 'N/A')}\n"
                    )
                    result_text += f"  • Status: {incident.get('status', 'N/A')}\n"
                    result_text += f"  • Severity: {incident.get('severity', 'N/A')}\n"
                    result_text += f"  • Urgency: {incident.get('urgency', 'N/A')}\n\n"

                    result_text += f"**Service:**\n"
                    result_text += (
                        f"  • Service: {incident.get('service_name', 'N/A')}\n"
                    )
                    result_text += (
                        f"  • Service ID: {incident.get('service_id', 'N/A')}\n\n"
                    )

                    result_text += f"**Assignment:**\n"
                    result_text += f"  • Assigned to: {incident.get('assigned_to_name', 'Unassigned')}\n"
                    result_text += (
                        f"  • Assigned to ID: {incident.get('assigned_to', 'N/A')}\n\n"
                    )

                    result_text += f"**Timeline:**\n"
                    result_text += f"  • Created: {incident.get('created_at', 'N/A')}\n"

                    if incident.get("acknowledged_at"):
                        result_text += (
                            f"  • Acknowledged: {incident.get('acknowledged_at')}\n"
                        )
                        result_text += f"  • Acknowledged by: {incident.get('acknowledged_by_name', 'N/A')}\n"

                    if incident.get("resolved_at"):
                        result_text += f"  • Resolved: {incident.get('resolved_at')}\n"
                        result_text += f"  • Resolved by: {incident.get('resolved_by_name', 'N/A')}\n"

                    result_text += f"\n**Metadata:**\n"
                    if incident.get("alert_key"):
                        result_text += f"  • Alert Key: {incident.get('alert_key')}\n"
                    if incident.get("escalation_policy_id"):
                        result_text += f"  • Escalation Policy: {incident.get('escalation_policy_id')}\n"

                    return {"content": [{"type": "text", "text": result_text}]}

                elif response.status == 404:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: Incident with ID '{incident_id}' not found",
                            }
                        ],
                        "isError": True,
                    }

                elif response.status == 401:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Authentication failed. Please check your API token.",
                            }
                        ],
                        "isError": True,
                    }

                else:
                    error_text = await response.text()
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: API request failed with status {response.status}\n{error_text}",
                            }
                        ],
                        "isError": True,
                    }

    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "isError": True,
        }


async def _get_incident_stats_impl(args: dict[str, Any]) -> dict[str, Any]:
    """
    Get incident statistics for a time range.

    Args:
        time_range: Time range for stats - "24h", "7d", "30d", or "all"

    Returns:
        Dictionary with statistics or error information
    """
    time_range = args.get("time_range", "24h")

    valid_ranges = ["24h", "7d", "30d", "all"]
    if time_range not in valid_ranges:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Invalid time_range. Must be one of: {', '.join(valid_ranges)}",
                }
            ],
            "isError": True,
        }

    try:
        headers = {
            "Authorization": f"Bearer {get_auth_token()}",
            "Content-Type": "application/json",
        }

        # ReBAC: Add org_id for tenant isolation (MANDATORY) and project_id (OPTIONAL)
        # Priority: 1. Argument 2. Context 3. Environment Variable
        org_id = args.get("org_id") or get_org_id() or os.getenv("inres_ORG_ID")
        project_id = args.get("project_id") or get_project_id()
        if org_id:
            headers["X-Org-ID"] = org_id
        if project_id:
            headers["X-Project-ID"] = project_id

        async with aiohttp.ClientSession() as session:
            url = f"{get_inres_api_base_url()}/incidents/stats"
            params = {"time_range": time_range}
            if org_id:
                params["org_id"] = org_id
            if project_id:
                params["project_id"] = project_id

            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    stats = await response.json()

                    # Format stats response
                    result_text = f"📈 **Incident Statistics ({time_range})**\n\n"
                    result_text += f"**Overall:**\n"
                    result_text += f"  • Total Incidents: {stats.get('total', 0)}\n"
                    result_text += f"  • Triggered: {stats.get('triggered', 0)}\n"
                    result_text += f"  • Acknowledged: {stats.get('acknowledged', 0)}\n"
                    result_text += f"  • Resolved: {stats.get('resolved', 0)}\n\n"

                    if stats.get("by_severity"):
                        result_text += f"**By Severity:**\n"
                        for severity, count in stats["by_severity"].items():
                            result_text += f"  • {severity}: {count}\n"
                        result_text += "\n"

                    if stats.get("avg_resolution_time"):
                        result_text += f"**Performance:**\n"
                        result_text += f"  • Avg Resolution Time: {stats.get('avg_resolution_time')}\n"
                        result_text += f"  • Avg Acknowledgment Time: {stats.get('avg_ack_time', 'N/A')}\n"

                    return {"content": [{"type": "text", "text": result_text}]}

                elif response.status == 401:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Authentication failed. Please check your API token.",
                            }
                        ],
                        "isError": True,
                    }

                else:
                    error_text = await response.text()
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: API request failed with status {response.status}\n{error_text}",
                            }
                        ],
                        "isError": True,
                    }

    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "isError": True,
        }


async def _get_current_time_impl(args: dict[str, Any]) -> dict[str, Any]:
    """
    Get the current date and time in ISO 8601 format (UTC).
    Useful for determining time ranges when querying incidents.

    Returns:
        Dictionary with current time and common time ranges
    """
    from datetime import datetime, timedelta

    # Get current time in UTC
    now = datetime.utcnow()

    # Format response with common time ranges
    result_text = f"**Current Time (UTC)**\n\n"
    result_text += f"Current: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    result_text += (
        f"1 hour ago: {(now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )
    result_text += (
        f"24 hours ago: {(now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )
    result_text += (
        f"7 days ago: {(now - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )

    return {"content": [{"type": "text", "text": result_text}]}


async def _search_incidents_impl(args: dict[str, Any]) -> dict[str, Any]:
    """
    Search incidents using full-text search.

    Args:
        query: Search query string (e.g., "CPU high", "database connection")
        status: Optional filter by status - "triggered", "acknowledged", "resolved", or "all" (default: "all")
        severity: Optional filter by severity - "critical", "error", "warning", "info"
        limit: Maximum number of incidents to return (default: 20, max: 100)

    Returns:
        Dictionary with search results ranked by relevance
    """
    query = args.get("query", "")
    status = args.get("status", "all")
    severity = args.get("severity", "")
    limit = args.get("limit", 20)

    if not query or query.strip() == "":
        return {
            "content": [
                {"type": "text", "text": "Error: Search query is required"}
            ],
            "isError": True,
        }

    # Validate limit
    if limit < 1 or limit > 100:
        return {
            "content": [
                {"type": "text", "text": "Error: Limit must be between 1 and 100"}
            ],
            "isError": True,
        }

    try:
        headers = {
            "Authorization": f"Bearer {get_auth_token()}",
            "Content-Type": "application/json",
        }

        # ReBAC: Add org_id for tenant isolation (MANDATORY) and project_id (OPTIONAL)
        # Priority: 1. Argument 2. Context 3. Environment Variable
        org_id = args.get("org_id") or get_org_id() or os.getenv("inres_ORG_ID")
        project_id = args.get("project_id") or get_project_id()
        if org_id:
            headers["X-Org-ID"] = org_id
        if project_id:
            headers["X-Project-ID"] = project_id

        # Build query parameters
        params = {"search": query, "limit": limit, "sort": "relevance"}

        # ReBAC: Add org_id and project_id to params
        if org_id:
            params["org_id"] = org_id
        if project_id:
            params["project_id"] = project_id

        if status != "all":
            params["status"] = status

        if severity:
            params["severity"] = severity

        async with aiohttp.ClientSession() as session:
            url = f"{get_inres_api_base_url()}/incidents"

            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    incidents = data.get("incidents", [])

                    # Format the response
                    if not incidents:
                        result_text = f"No incidents found matching '{query}'"
                        if status != "all":
                            result_text += f" with status '{status}'"
                        if severity:
                            result_text += f" and severity '{severity}'"
                    else:
                        result_text = f"🔍 Found {len(incidents)} incident(s) matching '{query}'\n"
                        result_text += f"(Sorted by relevance)\n\n"

                        for idx, incident in enumerate(incidents, 1):
                            result_text += f"**Incident #{idx}**\n"
                            result_text += f"  • ID: {incident.get('id', 'N/A')}\n"
                            result_text += (
                                f"  • Title: {incident.get('title', 'N/A')}\n"
                            )
                            result_text += (
                                f"  • Status: {incident.get('status', 'N/A')}\n"
                            )
                            result_text += (
                                f"  • Severity: {incident.get('severity', 'N/A')}\n"
                            )
                            result_text += (
                                f"  • Service: {incident.get('service_name', 'N/A')}\n"
                            )
                            result_text += (
                                f"  • Created: {incident.get('created_at', 'N/A')}\n"
                            )
                            result_text += f"  • Assigned to: {incident.get('assigned_to_name', 'Unassigned')}\n"

                            if incident.get("acknowledged_at"):
                                result_text += f"  • Acknowledged: {incident.get('acknowledged_at')}\n"
                            if incident.get("resolved_at"):
                                result_text += (
                                    f"  • Resolved: {incident.get('resolved_at')}\n"
                                )

                            result_text += "\n"

                    return {"content": [{"type": "text", "text": result_text}]}

                elif response.status == 401:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": "Error: Authentication failed. Please check your API token.",
                            }
                        ],
                        "isError": True,
                    }

                else:
                    error_text = await response.text()
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error: API request failed with status {response.status}\n{error_text}",
                            }
                        ],
                        "isError": True,
                    }

    except aiohttp.ClientError as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Network error occurred: {str(e)}\nPlease check if the inres API is running at {get_inres_api_base_url()}",
                }
            ],
            "isError": True,
        }

    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Unexpected error occurred: {str(e)}",
                }
            ],
            "isError": True,
        }


INCIDENT_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "get_incidents_by_time",
        "description": (
            "Fetch incidents from inres within a specific time range. Use this to retrieve "
            "incidents that occurred between start_time and end_time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": "ISO 8601 start time, e.g. 2024-01-01T00:00:00Z",
                },
                "end_time": {
                    "type": "string",
                    "description": "ISO 8601 end time, e.g. 2024-01-01T23:59:59Z",
                },
                "status": {
                    "type": "string",
                    "description": 'Optional: "triggered", "acknowledged", "resolved", or "all"',
                },
                "limit": {
                    "type": "integer",
                    "description": "Max incidents to return (default 50, max 1000)",
                },
            },
            "required": ["start_time", "end_time"],
        },
    },
    {
        "name": "get_incident_by_id",
        "description": "Fetch detailed information about a specific incident by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "The incident UUID or id"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "get_incident_stats",
        "description": "Get statistics about incidents in the system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": 'One of: "24h", "7d", "30d", "all"',
                },
            },
        },
    },
    {
        "name": "get_current_time",
        "description": (
            "Get the current date and time in ISO 8601 format (UTC). Use this to determine "
            "time ranges for querying incidents."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_incidents",
        "description": (
            "Search incidents using full-text search. Use this to find incidents by keywords, "
            "phrases, or descriptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "status": {
                    "type": "string",
                    "description": 'Optional: "triggered", "acknowledged", "resolved", "all"',
                },
                "severity": {
                    "type": "string",
                    "description": 'Optional: "critical", "error", "warning", "info"',
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20, max 100)",
                },
            },
            "required": ["query"],
        },
    },
]

INCIDENT_TOOL_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "get_incidents_by_time": _get_incidents_by_time_impl,
    "get_incident_by_id": _get_incident_by_id_impl,
    "get_incident_stats": _get_incident_stats_impl,
    "get_current_time": _get_current_time_impl,
    "search_incidents": _search_incidents_impl,
}


def filter_tool_schemas_by_name(
    schemas: List[Dict[str, Any]], allowed_names: Optional[List[str]]
) -> List[Dict[str, Any]]:
    """If allowed_names is set, return only schemas whose name is in the list."""
    if not allowed_names:
        return list(schemas)
    allowed = set(allowed_names)
    return [s for s in schemas if s.get("name") in allowed]


__all__ = [
    "INCIDENT_TOOL_SCHEMAS",
    "INCIDENT_TOOL_HANDLERS",
    "filter_tool_schemas_by_name",
    "_get_incidents_by_time_impl",
    "_get_incident_by_id_impl",
    "_get_incident_stats_impl",
    "_get_current_time_impl",
    "_search_incidents_impl",
    "set_auth_token",
    "get_auth_token",
    "set_org_id",
    "get_org_id",
    "set_project_id",
    "get_project_id",
]
