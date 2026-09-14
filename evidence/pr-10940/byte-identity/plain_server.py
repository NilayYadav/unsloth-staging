from dataclasses import dataclass

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
try:
    from fastmcp.tools.tool import ToolResult
except ImportError:
    from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, ResourceLink, TextContent, TextResourceContents
from pydantic import BaseModel

mcp = FastMCP("plain-structured")


class Report(BaseModel):
    title: str
    pages: int
    scores: list[float]


@dataclass
class Point:
    x: int
    y: int


@mcp.tool
def t_dict() -> dict:
    return {"rows": 3, "max": 41.5, "ok": True, "tags": ["a", "b"], "nested": {"x": None, "u": "ünïcödé ✓"}}


@mcp.tool
def t_list() -> list[int]:
    return [1, 2, 3]


@mcp.tool
def t_str() -> str:
    return "plain text with unicode ✓, tab\tand \x1b[31mansi\x1b[0m"


@mcp.tool
def t_int() -> int:
    return 42


@mcp.tool
def t_float() -> float:
    return 0.1 + 0.2


@mcp.tool
def t_bool() -> bool:
    return False


@mcp.tool
def t_none() -> None:
    return None


@mcp.tool
def t_model() -> Report:
    return Report(title="Q3", pages=12, scores=[1.5, 2.25])


@mcp.tool
def t_dataclass() -> Point:
    return Point(x=1, y=-2)


@mcp.tool
def t_big() -> dict:
    return {f"k{i}": {"v": i, "s": "x" * (i % 50)} for i in range(5000)}


@mcp.tool
def t_block_lookalike() -> dict:
    # plain data shaped like content blocks; no attachment block is returned
    return {
        "content": [{"type": "audio", "data": "UklGRiQAAABXQVZFZm10IBAAAAABAAEA", "mimeType": "audio/wav"}],
        "type": "image",
        "data": "iVBORw0KGgoAAAANSUhEUg==",
        "resource": {"uri": "file:///x.pdf", "blob": "JVBERi0xLjQK"},
    }


@mcp.tool
def t_structured_only() -> ToolResult:
    return ToolResult(content=[], structured_content={"a": 1, "b": [True, None]})


@mcp.tool
def t_text_and_structured() -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text="hi")], structured_content={"a": 1})


@mcp.tool
def t_empty_text_structured() -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text="")], structured_content={"k": ""})


@mcp.tool
def t_text_resource() -> ToolResult:
    return ToolResult(content=[EmbeddedResource(type="resource", resource=TextResourceContents(
        uri="file:///out/log.txt", mimeType="text/plain", text="line1\nline2"))])


@mcp.tool
def t_resource_link() -> ToolResult:
    return ToolResult(content=[ResourceLink(type="resource_link", uri="file:///out/table.csv", name="table.csv")])


@mcp.tool
def t_multi_text() -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text="one"), TextContent(type="text", text="two")],
                      structured_content={"n": 2})


@mcp.tool
def t_error() -> str:
    raise ToolError("boom: plain failure")


if __name__ == "__main__":
    mcp.run()
