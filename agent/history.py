"""Message-history helpers: build assistant/tool messages and parse tool arguments.

History is a plain list of OpenAI-style message dicts (system/user/assistant/tool).
We deliberately store only the assistant's content + tool_calls (NOT its reasoning),
mirroring ebpf-opt4's _assistant_dict — reasoning is for display, never sent back.
"""
import json


def assistant_dict(content: str, tool_calls) -> dict:
    """Assistant turn for history: content plus any structured tool_calls."""
    d = {"role": "assistant", "content": content or ""}
    if tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.name, "arguments": tc.arguments or "{}"}}
            for tc in tool_calls
        ]
    return d


def tool_result_msg(call_id: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def parse_args(raw):
    """Parse a tool-call arguments string into a dict.

    Returns (args_dict, error_str|None). A blank/None argument string is a valid {}.
    """
    if isinstance(raw, dict):
        return raw, None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    try:
        v = json.loads(raw)
    except Exception as e:
        return {}, str(e)
    return (v, None) if isinstance(v, dict) else ({}, "arguments were not a JSON object")
