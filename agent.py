"""
Email Agent — drives an Ollama model with the email MCP tools.

Usage:
    python agent.py
    python agent.py --model qwen2.5:14b
    python agent.py --url http://192.168.1.10:11434/v1
"""

import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

from openai import OpenAI


# ── MCP subprocess client (same pattern as csdb_mcp) ──────────────────────────

class MCPClient:
    def __init__(self, server_script: str = "server.py"):
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / server_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._id = 0
        self._lock = threading.Lock()
        self._initialize()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, method: str, params: dict = None) -> dict:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            msg["params"] = params
        with self._lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            while True:
                line = self.proc.stdout.readline()
                if line.strip():
                    return json.loads(line)

    def _initialize(self):
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "email-agent", "version": "0.1"},
        })
        self._send("notifications/initialized")

    def list_tools(self) -> list[dict]:
        return self._send("tools/list").get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content)

    def close(self):
        self.proc.terminate()


def _mcp_to_openai(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {}),
            },
        }
        for t in tools
    ]


# ── Agent REPL ────────────────────────────────────────────────────────────────

def run_agent(model: str, base_url: str):
    mcp = MCPClient()
    tools = mcp.list_tools()
    openai_tools = _mcp_to_openai(tools)

    client = OpenAI(base_url=base_url, api_key="ollama")

    print(f"Email Agent ready (model: {model}). Type 'quit' to exit.\n")
    print(f"Tools: {[t['name'] for t in tools]}\n")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful email assistant. "
                "Use the available tools to read, search, and send emails. "
                "When sending emails, confirm the recipient and subject before sending. "
                "Keep responses concise."
            ),
        }
    ]

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye.")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        while True:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                print(f"\nAssistant: {msg.content}\n")
                break

            for tc in msg.tool_calls:
                fn = tc.function
                print(f"  [tool] {fn.name}({fn.arguments})")
                try:
                    args = json.loads(fn.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = mcp.call_tool(fn.name, args)
                print(f"  [result] {result[:200]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

    mcp.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email agent powered by Ollama")
    parser.add_argument("--model", default="qwen2.5:latest", help="Ollama model name")
    parser.add_argument("--url", default="http://localhost:11434/v1", help="Ollama API base URL")
    args = parser.parse_args()
    run_agent(model=args.model, base_url=args.url)
