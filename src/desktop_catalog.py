"""Read-only tool and skill catalogs for the desktop settings panel."""

from .agent_loop import AgentLoop
from .parser import TOOLS
from .tool_registry import TOOL_FLOW_CONFIRM, TOOL_KIND_EXEC, ToolResult


def runtime_catalog(agent: AgentLoop) -> dict:
    """Describe registered tools and the current native and text entry points."""
    registry = agent.tool_registry
    provider = agent.config.provider
    enabled = agent._enabled_native_tool_names()
    allow = set(provider.native_tools)
    deny = set(provider.disabled_native_tools)
    custom_names = {tool.name for tool in agent.config.interfaces.custom_tools if tool.enabled}
    tools = []
    for schema in registry.schemas():
        name = schema["name"]
        kind = registry.kind(name)
        flow = registry.flow(name)
        tools.append({
            "name": name,
            "description": schema["description"],
            "kind": kind,
            "flow": flow,
            "risk": registry.risk(name),
            "native_enabled": name in enabled,
            "on_demand": "load_tools" in enabled and name not in enabled and name not in deny
            and (not allow or name in allow),
            "text_protocol": name in TOOLS,
            "extension": name in custom_names and kind == TOOL_KIND_EXEC and flow == TOOL_FLOW_CONFIRM,
        })
    skills = [
        {"name": skill.name, "description": skill.description, "path": skill.path, "source": skill.source}
        for skill in agent.skill_manager.list_skills(max_results=80)
    ]
    return {
        "tools": tools,
        "skills": skills,
        "enable_native_tools": provider.enable_native_tools,
        "native_tools": list(provider.native_tools),
        "disabled_native_tools": list(provider.disabled_native_tools),
        "skills_enabled": agent.skill_manager.config.enabled,
    }


def read_catalog_skill(agent: AgentLoop, name: str) -> dict:
    """Return a discovered skill body using the existing skill reader."""
    result = agent.skill_manager.read_skill(name)
    return {"success": not isinstance(result, ToolResult) or result.success, "content": str(result)}
