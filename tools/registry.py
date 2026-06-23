"""Tool registry + dispatch. Aggregates each tool module's TOOLS list into a single
name -> (fn, schema) map. dispatch() always returns text and never raises (ported from
ebpf-opt4 tools.dispatch). Adding a tool module = append it to _MODULES.
"""
from . import fs, search, shell, codeindex, nav, kernel
from .spec import ToolContext  # re-exported for convenience

# Tool modules contributing TOOLS = [(name, fn, schema), ...].
_MODULES = [fs, search, shell, codeindex, nav, kernel]

_REGISTRY: dict = {}      # name -> (fn, schema)


def _rebuild():
    _REGISTRY.clear()
    for mod in _MODULES:
        for name, fn, sch in getattr(mod, "TOOLS", []):
            _REGISTRY[name] = (fn, sch)


def register_module(mod):
    """Register an additional tool module (its TOOLS list)."""
    if mod not in _MODULES:
        _MODULES.append(mod)
    _rebuild()


def tools_for() -> list:
    """The OpenAI `tools=` schema list for all registered tools."""
    return [sch for (_fn, sch) in _REGISTRY.values()]


def tool_names() -> list:
    return sorted(_REGISTRY)


def dispatch(ctx: ToolContext, name: str, args: dict) -> str:
    """Execute a tool by name with kwargs; always returns text."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return f"(error: unknown tool {name!r}. Available: {', '.join(tool_names())})"
    fn = entry[0]
    try:
        return fn(ctx, **(args or {}))
    except TypeError as e:
        return f"(error: bad arguments for {name}: {e})"
    except Exception as e:                       # noqa: BLE001 — tools never raise into the loop
        return f"(error: tool {name} failed: {e})"


_rebuild()
