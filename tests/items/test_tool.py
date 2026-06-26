from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from pytest_httpx import HTTPXMock

from tests.conftest import _call_tool, _client, _mock_token

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")


class TestItemTools:
    async def test_server_registers_list_items_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then list_items is registered with an optional name param (nothing required)
        by_name = {tool.name: tool for tool in tools}
        assert "list_items" in by_name
        schema = by_name["list_items"].inputSchema
        assert schema.get("required", []) == []
        assert "name" in schema["properties"]

    async def test_list_items_maps_fields_without_name(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning an item for the catalog query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [self._item_fixture()]}})

        # When list_items is called with no name filter
        result = await _call_tool("list_items", {})

        # Then no Name clause was sent and the trimmed item fields are returned
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE" not in sent_sql
        assert result == [
            {
                "id": "4",
                "name": "Design",
                "type": "Service",
                "description": "Custom Design",
                "unit_price": 75,
            }
        ]

    async def test_list_items_passes_name_filter(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning an item for the filtered query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [self._item_fixture()]}})

        # When list_items is called with a name filter
        result = await _call_tool("list_items", {"name": "Design"})

        # Then the outbound query carried an escaped Name LIKE clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE '%Design%'" in sent_sql
        assert result[0]["id"] == "4"

    @staticmethod
    def _item_fixture() -> dict[str, Any]:
        return {
            "Id": "4",
            "Name": "Design",
            "Type": "Service",
            "Description": "Custom Design",
            "UnitPrice": 75,
        }
