"""Write results/inventory.json: workflows, paths, API endpoints, and MCP tools, read from code."""

# ============= Standard Library =============
import asyncio
from contextlib import AsyncExitStack

# ============= Local =============
from roiconsole.api.app import create_app
from roiconsole.common.results import write_results
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.paths.registry import PATHS_DICT
from roiconsole.runner.mcp_client import McpToolbox
from roiconsole.workflows.registry import load_tasks, load_workflows

# ============= Constants =============
API_PREFIXES = ("/api", "/health", "/metrics")


# ============= Helpers =============


def api_endpoints() -> list[str]:
    """Return "METHOD path" for every console API route."""
    endpoint_list = []
    for route in create_app(o_start_runner=False).routes:
        path = getattr(route, "path", "")
        if not path.startswith(API_PREFIXES):
            continue
        endpoint_list += [
            f"{method} {path}"
            for method in sorted(getattr(route, "methods", []) or [])
            if method != "HEAD"
        ]
    return sorted(endpoint_list)


async def mcp_tools() -> dict[str, list[str]]:
    """Return each MCP server's tool names, read from the servers over in-memory sessions."""
    async with AsyncExitStack() as stack:
        toolbox = await McpToolbox.in_memory(stack, build_backends(get_settings(), mode="dryrun"))
        specs_dict = await toolbox.list_tools()
    tools_dict: dict[str, list[str]] = {}
    for spec in specs_dict.values():
        tools_dict.setdefault(spec.server, []).append(spec.name)
    return {server: sorted(names) for server, names in sorted(tools_dict.items())}


# ============= Entry Point =============


def main() -> None:
    """Collect the inventory and write it."""
    workflows_dict = load_workflows()
    endpoint_list = api_endpoints()
    tools_dict = asyncio.run(mcp_tools())
    metrics_dict = {
        "workflows": {"count": len(workflows_dict), "ids": sorted(workflows_dict)},
        "execution_paths": {"count": len(PATHS_DICT), "names": sorted(PATHS_DICT)},
        "api_endpoints": {"count": len(endpoint_list), "endpoints": endpoint_list},
        "mcp_tools": {"count": sum(len(v) for v in tools_dict.values()), "by_server": tools_dict},
        "tasks": {"count": len(load_tasks())},
    }
    path = write_results("inventory", "inventory", None, metrics_dict, "generated from code")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
