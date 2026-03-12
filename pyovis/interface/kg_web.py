from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from pyovis.memory.graph_builder import KnowledgeGraphBuilder

logger = logging.getLogger(__name__)

_GRAPH_HTML_PATH = Path("/pyovis_memory/kg/graph.html")


def _build_graph_html(kg: KnowledgeGraphBuilder) -> str:
    stats = kg.get_stats()
    if stats["total_nodes"] == 0:
        return ""
    return kg.visualize(output_path=_GRAPH_HTML_PATH)


async def _index(request: Request) -> HTMLResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    stats = kg.get_stats()

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pyovis Knowledge Graph</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; }}
  .header {{ padding: 16px 24px; background: #161b22; border-bottom: 1px solid #30363d;
             display: flex; align-items: center; justify-content: space-between; }}
  .header h1 {{ font-size: 20px; font-weight: 600; }}
  .header h1 span {{ color: #58a6ff; }}
  .stats {{ display: flex; gap: 20px; font-size: 14px; color: #8b949e; }}
  .stats .num {{ color: #58a6ff; font-weight: 600; }}
  .toolbar {{ padding: 12px 24px; background: #161b22; border-bottom: 1px solid #30363d;
              display: flex; gap: 12px; align-items: center; }}
  .toolbar button {{ padding: 6px 14px; border-radius: 6px; border: 1px solid #30363d;
                     background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 13px; }}
  .toolbar button:hover {{ background: #30363d; }}
  .toolbar button.primary {{ background: #238636; border-color: #238636; color: #fff; }}
  .toolbar button.primary:hover {{ background: #2ea043; }}
  .empty {{ text-align: center; padding: 80px 20px; color: #8b949e; }}
  .empty h2 {{ font-size: 24px; margin-bottom: 12px; color: #c9d1d9; }}
  .empty p {{ font-size: 15px; }}
  iframe {{ width: 100%; height: calc(100vh - 110px); border: none; }}
</style>
</head>
<body>
<div class="header">
  <h1><span>Pyovis</span> Knowledge Graph</h1>
  <div class="stats">
    <span>Nodes: <span class="num">{stats["total_nodes"]}</span></span>
    <span>Edges: <span class="num">{stats["total_edges"]}</span></span>
    <span>Communities: <span class="num">{stats["total_communities"]}</span></span>
    <span>Code Symbols: <span class="num">{stats["total_code_symbols"]}</span></span>
  </div>
</div>
<div class="toolbar">
  <button class="primary" onclick="location.reload()">Refresh</button>
  <button onclick="fetch('/api/rebuild',{{method:'POST'}}).then(()=>location.reload())">Rebuild Graph</button>
  <button onclick="fetch('/api/detect-communities',{{method:'POST'}}).then(()=>location.reload())">Detect Communities</button>
</div>
"""
    if stats["total_nodes"] > 0:
        _build_graph_html(kg)
        html += '<iframe src="/graph.html"></iframe>'
    else:
        html += """<div class="empty">
  <h2>No graph data yet</h2>
  <p>Send messages to your Telegram bot to build the knowledge graph.</p>
</div>"""

    html += "</body></html>"
    return HTMLResponse(html)


async def _graph_html(request: Request) -> HTMLResponse:
    if _GRAPH_HTML_PATH.exists():
        return HTMLResponse(_GRAPH_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<p>Graph not generated yet.</p>", status_code=404)


async def _api_stats(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    return JSONResponse(kg.get_stats())


async def _api_rebuild(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    stats = kg.get_stats()
    if stats["total_nodes"] == 0:
        return JSONResponse({"status": "empty", "message": "No nodes to visualize"})
    path = _build_graph_html(kg)
    return JSONResponse({"status": "ok", "path": path})


async def _api_detect_communities(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    communities = kg.detect_communities()
    _build_graph_html(kg)
    return JSONResponse(
        {
            "status": "ok",
            "communities": len(communities),
        }
    )


async def _api_nodes(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    graph = kg._graph
    return JSONResponse(
        {
            "nodes": graph.get("nodes", {}),
            "total": len(graph.get("nodes", {})),
        }
    )


async def _api_edges(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    graph = kg._graph
    return JSONResponse(
        {
            "edges": graph.get("edges", []),
            "total": len(graph.get("edges", [])),
        }
    )


async def _api_code_symbols(request: Request) -> JSONResponse:
    kg: KnowledgeGraphBuilder = request.app.state.kg
    graph = kg._graph
    return JSONResponse(
        {
            "modules": graph.get("code_modules", {}),
            "symbols": graph.get("code_symbols", {}),
            "edges": graph.get("code_symbol_edges", []),
            "total_symbols": len(graph.get("code_symbols", {})),
        }
    )


_routes = [
    Route("/", _index),
    Route("/graph.html", _graph_html),
    Route("/api/stats", _api_stats),
    Route("/api/rebuild", _api_rebuild, methods=["POST"]),
    Route("/api/detect-communities", _api_detect_communities, methods=["POST"]),
    Route("/api/nodes", _api_nodes),
    Route("/api/edges", _api_edges),
    Route("/api/code-symbols", _api_code_symbols),
]


def create_app(kg: KnowledgeGraphBuilder | None = None) -> Starlette:
    if kg is None:
        kg = KnowledgeGraphBuilder()
    app = Starlette(routes=_routes)
    app.state.kg = kg
    return app


async def start_kg_web(
    kg: KnowledgeGraphBuilder | None = None, port: int = 8502
) -> None:
    import uvicorn

    app = create_app(kg)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("kg_web: starting on http://0.0.0.0:%d", port)
    await server.serve()
