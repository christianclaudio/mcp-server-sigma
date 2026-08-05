"""Enterprise assertion: no tool can raise a raw exception to the MCP caller."""

import asyncio
import inspect
import json
import os

os.environ.setdefault("SIGMA_CLIENT_ID", "fake_id")
os.environ.setdefault("SIGMA_CLIENT_SECRET", "fake_secret_value_1234")
os.environ.setdefault("SIGMA_API_BASE_URL", "http://localhost:9999")

from sigma_mcp.server import mcp  # noqa: E402


async def _test_all_tools():
    tools = mcp._tool_manager._tools
    print(f"Testing {len(tools)} registered tools with invalid args...")
    failures = []
    for name, tool_info in tools.items():
        fn = tool_info.fn if hasattr(tool_info, "fn") else tool_info
        sig = inspect.signature(fn)
        params = sig.parameters
        kwargs = {}
        for pname, p in params.items():
            ann = str(p.annotation)
            if "list" in ann:
                kwargs[pname] = []
            elif "dict" in ann:
                kwargs[pname] = {}
            elif p.annotation is str or "str" in ann:
                kwargs[pname] = "bogus-uuid-00000"
            elif "bool" in ann:
                kwargs[pname] = False
            elif "int" in ann:
                kwargs[pname] = 0
            elif p.default is not inspect.Parameter.empty:
                kwargs[pname] = p.default
            else:
                kwargs[pname] = "bogus"
        try:
            result = await fn(**kwargs)
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
            continue
        try:
            json.loads(result)
        except (TypeError, ValueError) as e:
            failures.append(f"{name}: returned non-JSON payload: {e}")
    return failures


def test_no_tool_raises_raw_exception():
    failures = asyncio.run(_test_all_tools())
    if failures:
        msg = f"{len(failures)} tool(s) raised raw exceptions:\n"
        msg += "\n".join(f"  {f}" for f in failures[:20])
        raise AssertionError(msg)


if __name__ == "__main__":
    failures = asyncio.run(_test_all_tools())
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  {f}")
    else:
        print("ALL tools handle errors gracefully (no raw exceptions)")
