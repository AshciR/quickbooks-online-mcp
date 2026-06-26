from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from pytest_httpx import HTTPXMock

from tests.conftest import UPSTASH_GET_URL, _call_tool, _client, _mock_token

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")


class TestCustomerTools:
    async def test_server_registers_search_customers_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then search_customers is registered with a single required string name param
        by_name = {tool.name: tool for tool in tools}
        assert "search_customers" in by_name
        schema = by_name["search_customers"].inputSchema
        assert schema["required"] == ["name"]
        assert schema["properties"]["name"]["type"] == "string"

    async def test_search_customers_escapes_name_and_maps_fields(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning a customer for a DisplayName query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Customer": [self._customer_fixture()]}})

        # When search_customers is called with a name containing a single quote
        result = await _call_tool("search_customers", {"name": "Amy's"})

        # Then the outbound query escaped the name and the trimmed customer fields are returned
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "DisplayName LIKE '%Amy''s%'" in sent_sql
        assert result == [
            {
                "id": "58",
                "display_name": "Amy's Bird Sanctuary",
                "company_name": "Amy's Bird Sanctuary",
                "email": "Birds@Intuit.com",
                "balance": 239.0,
            }
        ]

    async def test_search_customers_auth_expired_returns_bootstrap_string(self, httpx_mock: HTTPXMock) -> None:
        # Given Upstash holding no token bundle (QBO auth never bootstrapped)
        httpx_mock.add_response(url=UPSTASH_GET_URL, json={"result": None})

        # When the search_customers tool is called
        result = await _call_tool("search_customers", {"name": "Amy"})

        # Then it returns the re-run-bootstrap message as a string, not a raised traceback
        assert result == "QBO authorization expired — re-run scripts/bootstrap_oauth.py"

    @staticmethod
    def _customer_fixture() -> dict[str, Any]:
        return {
            "Id": "58",
            "DisplayName": "Amy's Bird Sanctuary",
            "CompanyName": "Amy's Bird Sanctuary",
            "PrimaryEmailAddr": {"Address": "Birds@Intuit.com"},
            "Balance": 239.0,
        }
