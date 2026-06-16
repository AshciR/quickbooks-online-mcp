from __future__ import annotations

import re
import time
import uuid
from datetime import date
from typing import Any

import httpx

from .config import Settings
from .token_store import TokenBundle, TokenStore

INTUIT_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
MINOR_VERSION = "75"
REFRESH_SKEW_SECONDS = 300

_ID_RE = re.compile(r"^\d+$")


class QBOClient:
    def __init__(
        self,
        settings: Settings,
        token_store: TokenStore,
        http: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._store = token_store
        self._http = http

    async def read(self, entity: str, entity_id: str) -> dict[str, Any]:
        validate_id(entity_id)
        return await self._request(
            "GET", self._company_path(f"/{entity}/{entity_id}")
        )

    async def create(self, entity: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            self._company_path(f"/{entity}"),
            json=payload,
            headers={"Request-Id": uuid.uuid4().hex, "Content-Type": "application/json"},
        )

    async def find_invoice_by_doc_number(self, doc_number: str) -> dict[str, Any] | None:
        """Resolve a human-facing invoice DocNumber (e.g. "1037") to its full object.

        Users know the document number printed on the invoice, not QBO's internal Id,
        so this wraps the private `_query` with an escaped `DocNumber` filter. Returns
        the first matching invoice, or None when no invoice carries that DocNumber.
        """
        sql = f"SELECT * FROM Invoice WHERE DocNumber = '{escape_qbo_string(doc_number)}'"
        body = await self._query(sql)
        invoices = body.get("QueryResponse", {}).get("Invoice", [])
        return invoices[0] if invoices else None

    async def _query(self, sql: str) -> dict[str, Any]:
        """Internal QBO SQL passthrough.

        Must NEVER be exposed via a public MCP tool or accept arbitrary
        caller-supplied SQL — public methods build queries with validated
        parameters and escape free-text values via escape_qbo_string().
        """
        return await self._request(
            "GET", self._company_path("/query"), params={"query": sql}
        )

    async def _ensure_fresh_token(self) -> TokenBundle:
        bundle = await self._store.load()
        if bundle is None:
            raise QBOAuthExpiredError()
        if bundle.access_expires_at - int(time.time()) < REFRESH_SKEW_SECONDS:
            bundle = await self._refresh(bundle)
        return bundle

    async def _refresh(self, bundle: TokenBundle) -> TokenBundle:
        resp = await self._http.post(
            INTUIT_TOKEN_URL,
            auth=(self._settings.intuit_client_id, self._settings.intuit_client_secret),
            data={"grant_type": "refresh_token", "refresh_token": bundle.refresh_token},
            headers={"Accept": "application/json"},
        )
        body: dict[str, Any]
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or "error" in body:
            if body.get("error") == "invalid_grant":
                raise QBOAuthExpiredError()
            resp.raise_for_status()
        new_bundle = TokenBundle(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or bundle.refresh_token,
            access_expires_at=int(time.time()) + int(body["expires_in"]),
        )
        await self._store.save(new_bundle)
        return new_bundle

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        _retry: bool = True,
    ) -> dict[str, Any]:
        bundle = await self._ensure_fresh_token()
        merged_params = dict(params or {})
        merged_params["minorversion"] = MINOR_VERSION
        merged_headers = {
            "Authorization": f"Bearer {bundle.access_token}",
            "Accept": "application/json",
        }
        if headers:
            merged_headers.update(headers)
        url = f"{self._settings.qbo_base_url}{path}"
        resp = await self._http.request(
            method, url, params=merged_params, json=json, headers=merged_headers
        )
        if resp.status_code == 401 and _retry:
            await self._refresh(bundle)
            return await self._request(
                method, path, params=params, json=json, headers=headers, _retry=False
            )
        if resp.status_code == 429:
            raise QBORateLimitError("QBO rate limit exceeded")
        if resp.status_code >= 400:
            self._raise_for_fault(resp)
            resp.raise_for_status()
        body = resp.json() if resp.content else {}
        if isinstance(body, dict) and "Fault" in body:
            self._raise_fault_from_body(body)
        return body

    def _company_path(self, suffix: str) -> str:
        return f"/v3/company/{self._settings.qbo_realm_id}{suffix}"

    @staticmethod
    def _raise_for_fault(resp: httpx.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            return
        if isinstance(body, dict) and "Fault" in body:
            QBOClient._raise_fault_from_body(body)

    @staticmethod
    def _raise_fault_from_body(body: dict[str, Any]) -> None:
        fault = body.get("Fault") or {}
        errors = fault.get("Error") or []
        if errors:
            err = errors[0]
            raise QBOFaultError(err.get("Message", ""), err.get("Detail", ""))
        raise QBOFaultError("Unknown QBO fault", "")


# --- helpers ---------------------------------------------------------------


class QBOError(Exception):
    pass


class QBOAuthExpiredError(QBOError):
    def __init__(self) -> None:
        super().__init__("QBO authorization expired — re-run scripts/bootstrap_oauth.py")


class QBORateLimitError(QBOError):
    pass


class QBOFaultError(QBOError):
    def __init__(self, message: str, detail: str) -> None:
        super().__init__(f"{message}: {detail}")
        self.message = message
        self.detail = detail


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"Invalid QBO entity id: {value!r}")
    return value


def validate_date(value: str) -> str:
    date.fromisoformat(value)
    return value


def escape_qbo_string(value: str) -> str:
    return value.replace("'", "''")
