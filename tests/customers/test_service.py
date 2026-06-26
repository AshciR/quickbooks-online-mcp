from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from pytest_httpx import HTTPXMock

from qbo_mcp.customers.service import CustomerService
from qbo_mcp.qbo_client import QBOClient

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")


class TestCustomerOperations:
    async def test_search_customers_escapes_name_and_filters_active(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning a customer list
        service = CustomerService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Customer": [{"Id": "58"}]}})

        # When searching with a name containing a single quote
        customers = await service.search_customers("Amy's")

        # Then the SQL escaped the name, filtered Active customers, and capped at 20
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "DisplayName LIKE '%Amy''s%'" in sent_sql
        assert "Active = true" in sent_sql
        assert "MAXRESULTS 20" in sent_sql
        assert customers == [{"Id": "58"}]

    async def test_search_customers_returns_empty_list_when_none_match(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning no customers
        service = CustomerService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

        # When searching for a name that matches nothing
        customers = await service.search_customers("Nobody")

        # Then an empty list is returned rather than an error
        assert customers == []
