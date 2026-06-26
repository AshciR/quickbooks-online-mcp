from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from pytest_httpx import HTTPXMock

from qbo_mcp.items.service import ItemService
from qbo_mcp.qbo_client import QBOClient

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")


class TestItemOperations:
    async def test_list_items_filters_active_sellable_types_without_name(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an item list
        service = ItemService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [{"Id": "4"}]}})

        # When listing items with no name filter
        items = await service.list_items()

        # Then the SQL constrained type + Active and added no Name clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Type IN ('Service', 'NonInventory', 'Inventory')" in sent_sql
        assert "Active = true" in sent_sql
        assert "Name LIKE" not in sent_sql
        assert items == [{"Id": "4"}]

    async def test_list_items_escapes_and_appends_name_filter(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an item list
        service = ItemService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [{"Id": "4"}]}})

        # When listing items with a name containing a single quote
        items = await service.list_items("O'Design")

        # Then the SQL appended an escaped Name LIKE clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE '%O''Design%'" in sent_sql
        assert items == [{"Id": "4"}]
