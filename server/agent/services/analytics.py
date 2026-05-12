"""
PGMQ Background Task for Incident Analytics

Runs alongside FastAPI server to consume incident analysis requests from PGMQ.
This runs in a background asyncio task within the main AI service.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from config import config
from anthropic_tool_loop import run_anthropic_tools_nonstreaming
from streaming.mcp_client import MCPToolManager
from tool_router import ToolRouter
from tools import (
    filter_tool_schemas_by_name,
    set_auth_token,
    set_org_id,
    set_project_id,
)
from tools.incidents import INCIDENT_TOOL_HANDLERS, INCIDENT_TOOL_SCHEMAS

logger = logging.getLogger(__name__)


class IncidentAnalyticsPGMQ:
    """Background PGMQ consumer for incident analytics"""

    def __init__(self):
        # Use centralized config
        self.db_url = config.database_url
        self.queue_name = "incident_analysis_queue"
        self.running = False

        if not self.db_url:
            logger.warning("DATABASE_URL not set - PGMQ incident analytics disabled")
            return

        logger.info(f"🤖 Incident Analytics PGMQ initialized (queue: {self.queue_name})")

    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def create_queue_if_not_exists(self):
        """Create PGMQ queue if it doesn't exist"""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT pgmq.create(%s);", (self.queue_name,))
                conn.commit()
            conn.close()
            logger.info(f"PGMQ queue '{self.queue_name}' ready")
        except Exception as e:
            logger.debug(f"Queue creation info: {e}")  # Likely already exists

    def read_message(self, vt: int = 300) -> Optional[Dict]:
        """Read a message from PGMQ queue (visibility timeout: 5 min)"""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM pgmq.read(%s, %s, %s);",
                    (self.queue_name, vt, 1),
                )
                result = cursor.fetchone()
            conn.close()
            return dict(result) if result else None
        except Exception as e:
            logger.error(f"Error reading PGMQ message: {e}")
            return None

    def delete_message(self, msg_id: int):
        """Delete message after successful processing"""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT pgmq.delete(%s, %s);",
                    (self.queue_name, msg_id),
                )
                conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error deleting PGMQ message {msg_id}: {e}")

    def build_analysis_prompt(self, incident: Dict[str, Any]) -> str:
        """Build analysis prompt from incident data"""
        title = incident.get("title", "Unknown Incident")
        description = incident.get("description", "")
        source = incident.get("source", "unknown")
        urgency = incident.get("urgency", "unknown")
        priority = incident.get("priority", "unknown")
        labels = incident.get("labels", {})
        raw_data = incident.get("raw_data", {})

        prompt = f"""Analyze this production incident and provide actionable insights make sumary is simple and clean, use your tools:
You can find related incident in 10 minutes before or after this incident to help you analyze this incident.
# Incident Details
- **Title**: {title}
- **Source**: {source}
- **Urgency**: {urgency}
- **Priority**: {priority}

# Description
{description}
"""

        if labels:
            prompt += "\n# Labels\n"
            for k, v in labels.items():
                prompt += f"- {k}: {v}\n"

        if raw_data:
            prompt += f"\n# Raw Data\n```json\n{json.dumps(raw_data, indent=2)}\n```\n"

        prompt += """

# Analysis Format

## 🔍 Summary
[2-3 sentence executive summary]

## 🔎 Probable Cause
[1-2 sentences on likely root cause]

## ⚡ Immediate Actions
1. [First action]
2. [Second action]

## 🛠️ Investigation Steps
1. [Where to look]
2. [What metrics to check]
3. [Commands to run]

## 📝 Additional Context
[Relevant patterns or context]

Keep it practical and action-oriented for on-call engineers.
"""
        return prompt

    def get_analytics_config(self) -> Dict[str, Any]:
        """Get AI analytics configuration from centralized config"""
        ai_config = config.ai_analytics

        logger.info(
            f"🤖 AI Analytics Config: model={ai_config.model}, "
            f"tools={len(ai_config.allowed_tools)}"
        )

        return {
            "model": ai_config.model,
            "allowed_tools": ai_config.allowed_tools,
        }

    async def analyze_incident(self, incident: Dict[str, Any]) -> str:
        """Analyze incident using Anthropic API + incident tools."""
        prompt = self.build_analysis_prompt(incident)
        ai_cfg = self.get_analytics_config()

        api_key = os.getenv("inres_API_KEY", "")
        if api_key:
            set_auth_token(api_key)
            logger.info(f"🔑 Auth token set for incident analysis (len={len(api_key)})")
        else:
            logger.warning("inres_API_KEY not set - API calls may fail")

        org_id = incident.get("organization_id") or incident.get("org_id")
        if org_id:
            set_org_id(org_id)
            logger.info(f"🏢 Org context set for incident analysis: {org_id}")
        else:
            logger.warning("No organization_id in incident data - API calls may fail")

        project_id = incident.get("project_id")
        if project_id:
            set_project_id(project_id)
            logger.info(f"📁 Project context set for incident analysis: {project_id}")

        filtered_schemas = filter_tool_schemas_by_name(
            INCIDENT_TOOL_SCHEMAS, ai_cfg["allowed_tools"]
        )
        if not filtered_schemas:
            logger.warning(
                "AI_ANALYTICS allowed_tools matched no incident tools; using full incident tool set"
            )
            filtered_schemas = list(INCIDENT_TOOL_SCHEMAS)
        router = ToolRouter(MCPToolManager(), INCIDENT_TOOL_HANDLERS, filtered_schemas)

        return await run_anthropic_tools_nonstreaming(
            user_prompt=prompt,
            model=ai_cfg["model"],
            max_tokens=4096,
            system_prompt=(
                "You are an expert SRE assistant. Use the provided tools when they help. "
                "Follow the user's requested output format."
            ),
            tool_router=router,
            max_turns=10,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    def update_incident_description(self, incident_id: str, analysis: str) -> bool:
        """Update incident with AI analysis"""
        try:
            conn = self.get_db_connection()
            with conn.cursor() as cursor:
                # Get current description
                cursor.execute(
                    "SELECT description FROM incidents WHERE id = %s",
                    (incident_id,),
                )
                result = cursor.fetchone()

                if not result:
                    logger.error(f"Incident {incident_id} not found")
                    return False

                current_desc = result["description"] or ""

                # Prepend analysis
                new_description = f"""# 🤖 AI Analysis

{analysis}

---

# Original Alert
{current_desc}
"""

                # Update
                cursor.execute(
                    """
                    UPDATE incidents
                    SET description = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_description, incident_id),
                )
                conn.commit()

            conn.close()
            logger.info(f"Updated incident {incident_id} with AI analysis")
            return True

        except Exception as e:
            logger.error(f"Failed to update incident {incident_id}: {e}", exc_info=True)
            return False

    async def process_message(self, message: Dict):
        """Process one incident analysis request"""
        msg_id = message.get("msg_id")
        message_data = message.get("message", {})

        incident_id = message_data.get("incident_id")
        incident_data = message_data.get("incident_data", {})

        logger.info(f"📥 Analyzing incident {incident_id}")

        try:
            # Analyze with Claude
            analysis = await self.analyze_incident(incident_data)

            # Update incident
            if self.update_incident_description(incident_id, analysis):
                # Success - delete message
                self.delete_message(msg_id)
                logger.info(f"Completed analysis for incident {incident_id}")
            else:
                logger.error(f"Failed to update incident {incident_id}")

        except Exception as e:
            logger.error(f"Error processing incident {incident_id}: {e}", exc_info=True)

    async def run_consumer(self):
        """Main consumer loop - runs in background"""
        if not self.db_url:
            logger.info("PGMQ incident analytics disabled (no DATABASE_URL)")
            return

        self.running = True
        self.create_queue_if_not_exists()

        logger.info("Starting PGMQ incident analytics consumer...")

        while self.running:
            try:
                # Read message
                message = self.read_message(vt=300)  # 5 min timeout

                if message:
                    await self.process_message(message)
                else:
                    # No messages - sleep
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"PGMQ consumer error: {e}", exc_info=True)
                await asyncio.sleep(5)

    def stop(self):
        """Stop the consumer"""
        self.running = False
        logger.info("Stopping PGMQ incident analytics consumer...")


# Global instance
_pgmq_consumer: Optional[IncidentAnalyticsPGMQ] = None


def get_pgmq_consumer() -> IncidentAnalyticsPGMQ:
    """Get or create PGMQ consumer instance"""
    global _pgmq_consumer
    if _pgmq_consumer is None:
        _pgmq_consumer = IncidentAnalyticsPGMQ()
    return _pgmq_consumer


async def start_pgmq_consumer():
    """Start PGMQ consumer in background - called at app startup"""
    consumer = get_pgmq_consumer()
    asyncio.create_task(consumer.run_consumer())
    logger.info("PGMQ incident analytics consumer started in background")


async def stop_pgmq_consumer():
    """Stop PGMQ consumer - called at app shutdown"""
    consumer = get_pgmq_consumer()
    consumer.stop()
    logger.info("PGMQ incident analytics consumer stopped")
