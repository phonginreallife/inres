#!/usr/bin/env python3
"""
Manual end-to-end check for /ws/chat.

Drives a real server with a real Claude session, so it needs the stack running
and a valid Supabase JWT. The automated suite lives in tests/ and needs neither.

Usage:
    python scripts/test_websocket.py --token "$JWT"
    python scripts/test_websocket.py --token "$JWT" --prompt "Show me incidents"
    python scripts/test_websocket.py --token "$JWT" --host ws://localhost:8002

/ws/secure/chat is not covered here: every frame must be signed by a device
certificate, which this script has no way to produce.
"""

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets


async def test_websocket(
    host: str,
    endpoint: str,
    prompt: str,
    token: str = "dev-token",
    org_id: str = "test-org",
):
    """Test a WebSocket endpoint."""
    
    # Build WebSocket URL
    ws_url = f"{host}/{endpoint}?token={token}&org_id={org_id}"
    
    print("=" * 60)
    print(f"WebSocket Test: {endpoint}")
    print("=" * 60)
    print(f"URL: {ws_url}")
    print(f"Prompt: {prompt}")
    print("-" * 60)
    
    try:
        async with websockets.connect(ws_url) as ws:
            # Wait for session_created
            print("Waiting for session...")
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            session_data = json.loads(response)
            
            if session_data.get("type") == "session_created":
                print(f"✓ Session created: {session_data.get('session_id', 'N/A')}")
                print(f"  Agent type: {session_data.get('agent_type', 'N/A')}")
                print(f"  Tools: {session_data.get('total_tools', 'N/A')}")
            else:
                print(f"? Unexpected first message: {session_data}")
            
            print("-" * 60)
            
            # Send chat message
            message = {
                "type": "chat",
                "prompt": prompt,
                "org_id": org_id,
            }
            await ws.send(json.dumps(message))
            print(f"→ Sent: {prompt[:50]}...")
            print("-" * 60)
            print("Streaming Response:")
            print()
            
            # Receive streaming response
            full_response = ""
            
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=60)
                    event = json.loads(response)
                    
                    event_type = event.get("type")
                    
                    if event_type == "delta":
                        content = event.get("content", "")
                        print(content, end="", flush=True)
                        full_response += content
                        
                    elif event_type == "text":
                        # Legacy block-level output
                        content = event.get("content", "")
                        print(content, end="", flush=True)
                        full_response += content
                        
                    elif event_type == "tool_use":
                        tool_name = event.get("name", "unknown")
                        print(f"\n[🔧 Tool: {tool_name}]", flush=True)
                        
                    elif event_type == "tool_result":
                        content = event.get("content", "")[:100]
                        is_error = event.get("is_error", False)
                        status = "❌" if is_error else "✓"
                        print(f"[{status} Result: {content}...]", flush=True)
                        
                    elif event_type == "thinking":
                        content = event.get("content", "")[:50]
                        print(f"\n[💭 Thinking: {content}...]", flush=True)

                    elif event_type == "session_init":
                        print(f"[Claude session: {event.get('session_id')}]", flush=True)

                    elif event_type == "processing":
                        print("[Starting...]", flush=True)

                    elif event_type == "todo_update":
                        print(f"\n[📋 {len(event.get('todos', []))} todos]", flush=True)

                    elif event_type == "permission_request":
                        tool = event.get("tool_name", "?")
                        print(f"\n[🔐 Approval requested for {tool} - auto-approving]", flush=True)
                        await ws.send(json.dumps({
                            "type": "permission_response",
                            "request_id": event.get("request_id"),
                            "allow": "yes",
                        }))

                    elif event_type == "permission_timeout":
                        print("\n[⏰ Approval timed out]", flush=True)


                    elif event_type == "complete":
                        print("\n")
                        print("-" * 60)
                        print("✓ Response complete")
                        break
                        
                    elif event_type == "error":
                        error = event.get("error", "Unknown error")
                        print(f"\n❌ Error: {error}")
                        break
                        
                    elif event_type == "interrupted":
                        print("\n⚠️ Interrupted")
                        break
                        
                    elif event_type == "ping":
                        # Respond to ping
                        await ws.send(json.dumps({"type": "pong"}))
                        
                    else:
                        print(f"\n[? Unknown event: {event_type}]", flush=True)
                        
                except asyncio.TimeoutError:
                    print("\n⏰ Timeout waiting for response")
                    break
            
            print(f"Response length: {len(full_response)} chars")
            print("=" * 60)
            
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"❌ Connection failed: {e}")
        if e.status_code == 4001:
            print("   Authentication failed - check your token")
    except ConnectionRefusedError:
        print(f"❌ Connection refused - is the server running at {host}?")
    except Exception as e:
        print(f"❌ Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Manual end-to-end check for /ws/chat")
    parser.add_argument(
        "--host",
        default="ws://localhost:8002",
        help="WebSocket host URL (default: ws://localhost:8002)"
    )
    parser.add_argument(
        "--prompt",
        default="Hello! What can you help me with?",
        help="Prompt to send"
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Supabase JWT - the server rejects anything else"
    )
    parser.add_argument(
        "--org-id",
        default="",
        help="Organization ID for tenant isolation"
    )

    args = parser.parse_args()

    asyncio.run(
        test_websocket(args.host, "ws/chat", args.prompt, args.token, args.org_id)
    )


if __name__ == "__main__":
    main()
