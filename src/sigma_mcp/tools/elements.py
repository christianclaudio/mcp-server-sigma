"""Elements domain sub-server for Sigma MCP."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import FastMCP

from sigma_mcp.tools.common import (
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _invalid_request,
    sigma_tool,
)
from sigma_mcp.tools.common import (
    resolve_client as get_client,
)

elements_server = FastMCP(
    "sigma-elements",
    instructions="Workbook pages, elements, queries, columns, controls, materializations, and documentation.",
)


@elements_server.tool(
    name="sigma_list_workbook_page_elements", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_page_elements(workbook_id: str, page_id: str) -> str:
    """List elements on a specific page of a workbook."""
    return json.dumps(await (await get_client()).list_workbook_page_elements(workbook_id, page_id), indent=2)


list_workbook_page_elements = sigma_list_workbook_page_elements


@elements_server.tool(
    name="sigma_list_workbook_elements", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_elements(workbook_id: str) -> str:
    """List all elements (tables, charts, controls, etc.) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_elements(workbook_id), indent=2)


list_workbook_elements = sigma_list_workbook_elements


@elements_server.tool(
    name="sigma_list_workbook_columns", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_columns(workbook_id: str) -> str:
    """List all columns across all elements in a workbook, including formulas and types."""
    return json.dumps(await (await get_client()).list_workbook_columns(workbook_id), indent=2)


list_workbook_columns = sigma_list_workbook_columns


@elements_server.tool(
    name="sigma_list_workbook_queries", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_queries(workbook_id: str) -> str:
    """List generated SQL queries for all elements in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_queries(workbook_id), indent=2)


list_workbook_queries = sigma_list_workbook_queries


@elements_server.tool(
    name="sigma_list_workbook_controls", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_controls(workbook_id: str) -> str:
    """List control elements (filters, parameters) in a workbook."""
    return json.dumps(await (await get_client()).list_workbook_controls(workbook_id), indent=2)


list_workbook_controls = sigma_list_workbook_controls


@elements_server.tool(
    name="sigma_list_workbook_sources", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_workbook_sources(workbook_id: str) -> str:
    """List data sources used by a workbook."""
    return json.dumps(await (await get_client()).list_workbook_sources(workbook_id), indent=2)


list_workbook_sources = sigma_list_workbook_sources


@elements_server.tool(
    name="sigma_swap_workbook_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"elements", "mutation"}
)
@sigma_tool
async def sigma_swap_workbook_sources(
    workbook_id: str,
    connection_mapping: list[dict[str, Any]] | None = None,
    source_mapping: list[dict[str, Any]] | None = None,
) -> str:
    """Swap data sources for a workbook. Use connectionMapping for connection-level swaps, sourceMapping for table-level swaps."""
    body: dict[str, Any] = {}
    if connection_mapping:
        body["connectionMapping"] = connection_mapping
    if source_mapping:
        body["sourceMapping"] = source_mapping
    return json.dumps(await (await get_client()).swap_workbook_sources(workbook_id, body), indent=2)


swap_workbook_sources = sigma_swap_workbook_sources


@elements_server.tool(name="sigma_get_element_query", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"})
@sigma_tool
async def sigma_get_element_query(workbook_id: str, element_id: str) -> str:
    """Get the generated SQL query for a specific element in a workbook."""
    return json.dumps(await (await get_client()).get_element_query(workbook_id, element_id), indent=2)


get_element_query = sigma_get_element_query


@elements_server.tool(
    name="sigma_get_element_columns", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_get_element_columns(workbook_id: str, element_id: str) -> str:
    """List columns for a specific element in a workbook."""
    return json.dumps(await (await get_client()).get_element_columns(workbook_id, element_id), indent=2)


get_element_columns = sigma_get_element_columns


@elements_server.tool(
    name="sigma_materialize_element", annotations=ANNOTATION_WRITE_SAFE, tags={"elements", "mutation"}
)
@sigma_tool
async def sigma_materialize_element(workbook_id: str, element_id: str) -> str:
    """Trigger materialization for a workbook. Pass elementId in body if needed."""
    return json.dumps(await (await get_client()).materialize_workbook(workbook_id, {"elementId": element_id}), indent=2)


materialize_element = sigma_materialize_element


@elements_server.tool(
    name="sigma_get_materialization_job", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_get_materialization_job(workbook_id: str, job_id: str) -> str:
    """Check status of a materialization job."""
    return json.dumps(await (await get_client()).get_materialization_job(workbook_id, job_id), indent=2)


get_materialization_job = sigma_get_materialization_job


@elements_server.tool(
    name="sigma_list_materialization_schedules", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_materialization_schedules(workbook_id: str) -> str:
    """List materialization schedules for a workbook."""
    return json.dumps(await (await get_client()).list_materialization_schedules(workbook_id), indent=2)


list_materialization_schedules = sigma_list_materialization_schedules


@elements_server.tool(
    name="sigma_swap_template_sources", annotations=ANNOTATION_WRITE_SAFE, tags={"elements", "mutation"}
)
@sigma_tool
async def sigma_swap_template_sources(
    template_id: str,
    connection_mapping: list[dict[str, Any]] | None = None,
    source_mapping: list[dict[str, Any]] | None = None,
) -> str:
    """Swap data sources on a template. Use connectionMapping for connection-level swaps, sourceMapping for table-level swaps."""
    body: dict[str, Any] = {}
    if connection_mapping:
        body["connectionMapping"] = connection_mapping
    if source_mapping:
        body["sourceMapping"] = source_mapping
    return json.dumps(await (await get_client()).swap_template_sources(template_id, body), indent=2)


swap_template_sources = sigma_swap_template_sources


@elements_server.tool(
    name="sigma_list_source_swap_policies", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_source_swap_policies() -> str:
    """List all source swap policies (rules for automatic source swapping on deployment)."""
    return json.dumps(await (await get_client()).list_source_swap_policies(), indent=2)


list_source_swap_policies = sigma_list_source_swap_policies


@elements_server.tool(
    name="sigma_get_source_swap_policy", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_get_source_swap_policy(policy_id: str) -> str:
    """Get a source swap policy."""
    return json.dumps(await (await get_client()).get_source_swap_policy(policy_id), indent=2)


get_source_swap_policy = sigma_get_source_swap_policy


@elements_server.tool(
    name="sigma_create_source_swap_policy", annotations=ANNOTATION_WRITE_SAFE, tags={"elements", "mutation"}
)
@sigma_tool
async def sigma_create_source_swap_policy(body: dict[str, Any]) -> str:
    """Create a source swap policy for automated deployments."""
    return json.dumps(await (await get_client()).create_source_swap_policy(body), indent=2)


create_source_swap_policy = sigma_create_source_swap_policy


@elements_server.tool(
    name="sigma_materialize_and_wait", annotations=ANNOTATION_WRITE_SAFE, tags={"elements", "mutation"}
)
@sigma_tool
async def sigma_materialize_and_wait(workbook_id: str, element_id: str, timeout_seconds: int = 300) -> str:
    """Trigger materialization and poll until complete or timeout."""
    if not workbook_id or not workbook_id.strip():
        return _invalid_request("workbook_id is required")
    if not element_id or not element_id.strip():
        return _invalid_request("element_id is required")
    from sigma_mcp import server as _srv

    c = await _srv.get_client()
    job = await c.materialize_workbook(workbook_id, {"elementId": element_id})
    job_id = job.get("materializationId") or job.get("jobId") or job.get("id") if isinstance(job, dict) else None
    if not job_id:
        return json.dumps({"error": "Could not extract job ID from response", "raw": job})

    deadline = _srv.time.time() + timeout_seconds
    status: Any = None
    while _srv.time.time() < deadline:
        status = await c.get_materialization_job(workbook_id, job_id)
        state = (status.get("status", "") if isinstance(status, dict) else "").lower()
        if state in ("completed", "complete", "done"):
            return json.dumps({"status": "completed", "job": status}, indent=2)
        if state in ("failed", "error", "cancelled"):
            return json.dumps({"status": state, "job": status}, indent=2)
        await _srv.asyncio.sleep(5)

    return json.dumps({"status": "timeout", "last_check": status}, indent=2)


materialize_and_wait = sigma_materialize_and_wait


@elements_server.tool(
    name="sigma_list_all_input_tables", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"}
)
@sigma_tool
async def sigma_list_all_input_tables() -> str:
    """Scan all workbooks to find input-table elements.

    Returns a list of input tables with their workbook, page, and element context.
    Errors on individual workbooks/pages are collected rather than silently swallowed.
    """
    c = await get_client()
    all_workbooks = await c.list_all_workbooks()

    input_tables: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(5)

    async def scan_workbook(wb: dict[str, Any]) -> None:
        wb_id = wb.get("workbookId", "")
        wb_name = wb.get("name", "")
        async with sem:
            try:
                pages_data = await c.list_workbook_pages(wb_id)
                pages = pages_data.get("entries", []) if isinstance(pages_data, dict) else []
            except Exception as e:
                errors.append({"workbookId": wb_id, "workbookName": wb_name, "stage": "pages", "error": str(e)})
                return

            for page in pages:
                page_id = page.get("pageId", "")
                page_name = page.get("name", "")
                try:
                    elements_data = await c.list_workbook_page_elements(wb_id, page_id)
                    elements = elements_data.get("entries", []) if isinstance(elements_data, dict) else []
                except Exception as e:
                    errors.append({"workbookId": wb_id, "pageId": page_id, "stage": "elements", "error": str(e)})
                    continue

                for el in elements:
                    if el.get("type") == "input-table":
                        input_tables.append(
                            {
                                "workbookId": wb_id,
                                "workbookName": wb_name,
                                "pageId": page_id,
                                "pageName": page_name,
                                "elementId": el.get("elementId", ""),
                                "elementName": el.get("name", ""),
                            }
                        )

    await asyncio.gather(*[scan_workbook(wb) for wb in all_workbooks])

    return json.dumps(
        {
            "total_input_tables": len(input_tables),
            "input_tables": input_tables,
            "workbooks_scanned": len(all_workbooks),
            "errors": errors,
        },
        indent=2,
    )


list_all_input_tables = sigma_list_all_input_tables


@elements_server.tool(name="sigma_formula_pitfalls", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"})
@sigma_tool
async def sigma_formula_pitfalls() -> str:
    """Return a curated reference of common Sigma formula pitfalls — column reference syntax, type requirements, NULL handling, aggregate vs row-level context, date function argument order, and metrics vs calculated columns. Use this before writing any Sigma formula expression."""
    import importlib.resources

    ref = importlib.resources.files("sigma_mcp").joinpath("reference/formulas.md")
    # Wrapped in JSON to preserve the server-wide contract that every tool
    # returns parseable JSON on both success and failure.
    return json.dumps({"format": "markdown", "content": ref.read_text(encoding="utf-8")}, indent=2)


formula_pitfalls = sigma_formula_pitfalls


@elements_server.tool(name="sigma_search_docs", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"})
@sigma_tool
async def sigma_search_docs(query: str) -> str:
    """Search Sigma Computing documentation using AI-powered semantic search. Returns relevant doc passages with source URLs. Use this to answer questions about Sigma features, configuration, formulas, administration, embedding, and best practices.

    read_only_hint: True
    """
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "searchDocs", "arguments": {"query": query}},
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            "https://help.sigmacomputing.com/_mcp/server",
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
    if resp.status_code != 200:
        return json.dumps({"error": {"type": "docs_search_failed", "status": resp.status_code}})
    # Fern MCP returns SSE; parse the data line
    text = resp.text
    for line in text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            result = data.get("result", {})
            content_list = result.get("content", [])
            if content_list:
                return json.dumps({"format": "markdown", "content": content_list[0].get("text", "")}, indent=2)
    return json.dumps({"error": {"type": "docs_search_empty", "message": "No results found"}})


search_docs = sigma_search_docs


@elements_server.tool(name="sigma_get_doc_page", annotations=ANNOTATION_READ_ONLY, tags={"elements", "read_only"})
@sigma_tool
async def sigma_get_doc_page(page_slug: str) -> str:
    """Fetch a specific Sigma documentation page as clean Markdown. Pass the page slug (e.g. 'create-a-workbook') or a section path (e.g. 'docs/create-a-workbook'). Returns the full page content.

    read_only_hint: True
    """
    import httpx

    slug = page_slug.strip("/")
    if slug.startswith("https://help.sigmacomputing.com/"):
        slug = slug.replace("https://help.sigmacomputing.com/", "")
    if not slug.startswith("docs/"):
        slug = f"docs/{slug}"
    if slug.endswith(".md"):
        slug = slug[:-3]
    url = f"https://help.sigmacomputing.com/{slug}.md"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
        resp = await http.get(url)
    if resp.status_code != 200:
        return json.dumps({"error": {"type": "page_not_found", "slug": page_slug, "status": resp.status_code}})
    return json.dumps({"format": "markdown", "content": resp.text}, indent=2)


get_doc_page = sigma_get_doc_page
