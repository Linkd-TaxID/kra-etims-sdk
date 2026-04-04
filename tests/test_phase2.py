"""
Phase 2 Test Suite
==================
Covers:
  - middleware.py  : async-aware sanitize_kra_url decorator
  - exceptions.py  : full error taxonomy + _handle_error_response mapping
  - tax.py         : zero-math tax calculator (all 5 bands)
  - qr.py          : render_kra_qr_string
  - async_client.py: api_key parity + concurrent flush_offline_queue
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# middleware — async-aware decorator
# ---------------------------------------------------------------------------

class TestSanitizeKraUrl:
    def test_sync_strips_whitespace(self):
        from kra_etims.middleware import sanitize_kra_url

        @sanitize_kra_url
        def my_func(path: str, token: str = "x") -> tuple:
            return (path, token)

        assert my_func("  /v2/test  ", token="  tok  ") == ("/v2/test", "tok")

    def test_sync_preserves_non_string_args(self):
        from kra_etims.middleware import sanitize_kra_url

        @sanitize_kra_url
        def my_func(a, b=None):
            return (a, b)

        assert my_func(42, b=3.14) == (42, 3.14)

    def test_async_returns_coroutine(self):
        from kra_etims.middleware import sanitize_kra_url

        @sanitize_kra_url
        async def my_async_func(path: str) -> str:
            return path

        result = my_async_func("  /v2/async  ")
        assert asyncio.iscoroutine(result)
        result.close()  # clean up unawaited coroutine

    @pytest.mark.asyncio
    async def test_async_strips_whitespace(self):
        from kra_etims.middleware import sanitize_kra_url

        @sanitize_kra_url
        async def my_async_func(path: str, token: str = "x") -> tuple:
            return (path, token)

        result = await my_async_func("  /v2/etims  ", token="  bearer123  ")
        assert result == ("/v2/etims", "bearer123")

    @pytest.mark.asyncio
    async def test_async_preserves_non_string_args(self):
        from kra_etims.middleware import sanitize_kra_url

        @sanitize_kra_url
        async def my_async_func(a, b=None):
            return (a, b)

        assert await my_async_func(99, b=True) == (99, True)


# ---------------------------------------------------------------------------
# exceptions — error taxonomy
# ---------------------------------------------------------------------------

class TestExceptionTaxonomy:
    def test_all_exceptions_subclass_base(self):
        from kra_etims.exceptions import (
            KRAeTIMSError, KRAeTIMSAuthError, KRAConnectivityTimeoutError,
            TIaaSUnavailableError, TIaaSAmbiguousStateError,
            KRAValidationError, KRAInvalidPINError, KRAVSCUMemoryFullError,
            KRADuplicateInvoiceError, KRAInvalidItemCodeError,
            KRAInvalidBranchError, KRAServerError,
        )
        for exc_class in [
            KRAeTIMSAuthError, KRAConnectivityTimeoutError,
            TIaaSUnavailableError, TIaaSAmbiguousStateError,
            KRAValidationError, KRAInvalidPINError, KRAVSCUMemoryFullError,
            KRADuplicateInvoiceError, KRAInvalidItemCodeError,
            KRAInvalidBranchError, KRAServerError,
        ]:
            assert issubclass(exc_class, KRAeTIMSError), (
                f"{exc_class.__name__} must subclass KRAeTIMSError"
            )

    def test_validation_errors_subclass_kra_validation_error(self):
        from kra_etims.exceptions import (
            KRAValidationError, KRAInvalidPINError,
            KRAInvalidItemCodeError, KRAInvalidBranchError,
        )
        for exc_class in [KRAInvalidPINError, KRAInvalidItemCodeError, KRAInvalidBranchError]:
            assert issubclass(exc_class, KRAValidationError)

    def test_kra_error_map_covers_known_codes(self):
        from kra_etims.exceptions import KRA_ERROR_MAP
        expected_codes = {"01", "10", "11", "12", "13", "14", "20", "96", "99"}
        assert expected_codes.issubset(set(KRA_ERROR_MAP.keys()))

    def test_default_messages_are_informative(self):
        from kra_etims.exceptions import KRAInvalidPINError, KRAVSCUMemoryFullError
        assert "A123456789B" in str(KRAInvalidPINError())
        assert "11" in str(KRAVSCUMemoryFullError())


# ---------------------------------------------------------------------------
# _handle_error_response integration
# ---------------------------------------------------------------------------

class TestHandleErrorResponse:
    def _make_client(self):
        from kra_etims.client import KRAeTIMSClient
        return KRAeTIMSClient("id", "secret", base_url="http://test.local")

    def test_success_code_00_returns_none(self):
        client = self._make_client()
        # Should not raise
        client._handle_error_response({"resultCd": "00", "resultMsg": "It is succeeded"})

    def test_code_10_raises_invalid_pin(self):
        from kra_etims.exceptions import KRAInvalidPINError
        client = self._make_client()
        with pytest.raises(KRAInvalidPINError, match="Invalid PIN"):
            client._handle_error_response({"resultCd": "10", "resultMsg": "PIN not found"})

    def test_code_11_raises_vscu_memory_full(self):
        from kra_etims.exceptions import KRAVSCUMemoryFullError
        client = self._make_client()
        with pytest.raises(KRAVSCUMemoryFullError):
            client._handle_error_response({"resultCd": "11", "resultMsg": "Memory is full"})

    def test_code_12_raises_duplicate_invoice(self):
        from kra_etims.exceptions import KRADuplicateInvoiceError
        client = self._make_client()
        with pytest.raises(KRADuplicateInvoiceError):
            client._handle_error_response({"resultCd": "12", "resultMsg": "Duplicate"})

    def test_unknown_code_raises_base_error(self):
        from kra_etims.exceptions import KRAeTIMSError
        client = self._make_client()
        with pytest.raises(KRAeTIMSError, match=r"KRA Error \[42\]"):
            client._handle_error_response({"resultCd": "42", "resultMsg": "Bizarre error"})

    def test_missing_result_cd_treated_as_success(self):
        client = self._make_client()
        # No resultCd key → defaults to "00" → no exception
        client._handle_error_response({"data": {"qrCode": "abc"}})


# ---------------------------------------------------------------------------
# tax.py — Zero-Math calculator
# ---------------------------------------------------------------------------

class TestCalculateItem:
    def test_band_b_standard_vat_inclusive_math(self):
        # Band B = Standard VAT (16%) — KRA eTIMS Technical Specification v2.0 §4.1.
        # 5000.00 inclusive → taxable = 5000 / 1.16 = 4310.34, tax = 689.66
        from kra_etims.tax import calculate_item
        item = calculate_item("Maize", "HS110100", 5000, "B")
        assert item.totAmt == Decimal("5000.00")
        assert item.taxblAmt == Decimal("4310.34")
        assert item.taxAmt == Decimal("689.66")
        # Core invariant: taxable + tax == total
        assert item.taxblAmt + item.taxAmt == item.totAmt

    def test_band_a_exempt(self):
        # Band A = Exempt (0% VAT, no input credit) — KRA eTIMS TIS v2.0 §4.1.
        # Exempt items: taxblAmt == totAmt, taxAmt == 0.
        from kra_etims.tax import calculate_item
        item = calculate_item("Basic Foodstuff", "HS110100", 5000, "A")
        assert item.totAmt == Decimal("5000.00")
        assert item.taxblAmt == Decimal("5000.00")
        assert item.taxAmt == Decimal("0.00")

    def test_band_c_zero_rated(self):
        # Band C = Zero-Rated (0% VAT, input credit allowed) — KRA TIS v2.0 §4.1.
        # Zero-rated exports: taxblAmt == totAmt, taxAmt == 0.
        from kra_etims.tax import calculate_item
        item = calculate_item("Export Goods", "HS270900", 1080, "C")
        assert item.totAmt == Decimal("1080.00")
        assert item.taxblAmt == Decimal("1080.00")
        assert item.taxAmt == Decimal("0.00")

    def test_band_e_special_rate_8pct_inclusive(self):
        # Band E = Special Rate (8% VAT) — Kenya VAT Act, petroleum/LPG.
        # 200.00 inclusive → taxable = 200/1.08 = 185.19, tax = 14.81
        from kra_etims.tax import calculate_item
        item = calculate_item("LPG Cylinder", "HS271111", 200, "E")
        assert item.totAmt == Decimal("200.00")
        assert item.taxblAmt == Decimal("185.19")
        assert item.taxAmt == Decimal("14.81")
        assert item.taxblAmt + item.taxAmt == item.totAmt

    def test_band_d_exempt(self):
        # Band D = Exempt (0% VAT, no credit) — KRA eTIMS Technical Specification v2.0.
        from kra_etims.tax import calculate_item
        item = calculate_item("Export Goods", "HS999999", 10000, "D")
        assert item.taxAmt == Decimal("0.00")

    def test_band_e_special_rate_8pct(self):
        # Band E = Special Rate (8% VAT) — KRA eTIMS Technical Specification v2.0.
        # 500.00 inclusive → taxable = 500/1.08 = 462.96, tax = 37.04
        from kra_etims.tax import calculate_item
        item = calculate_item("Bank Charges", "SRV001", 500, "E")
        assert item.totAmt == Decimal("500.00")
        assert item.taxblAmt == Decimal("462.96")
        assert item.taxAmt == Decimal("37.04")
        assert item.taxblAmt + item.taxAmt == item.totAmt

    def test_quantity_multiplies_correctly(self):
        from kra_etims.tax import calculate_item
        item = calculate_item("Widget", "WDG001", Decimal("100"), "A", qty=3)
        assert item.qty == Decimal("3.00")
        assert item.totAmt == Decimal("300.00")
        assert item.taxblAmt + item.taxAmt == item.totAmt

    def test_exclusive_pricing_mode(self):
        from kra_etims.tax import calculate_item
        # NET price 1000, Band B (16% standard) → totAmt = 1160
        item = calculate_item("Service", "SRV002", 1000, "B", price_is_inclusive=False)
        assert item.taxblAmt == Decimal("1000.00")
        assert item.taxAmt == Decimal("160.00")
        assert item.totAmt == Decimal("1160.00")

    def test_invalid_band_raises_value_error(self):
        from kra_etims.tax import calculate_item
        with pytest.raises(ValueError, match="Unknown tax_band"):
            calculate_item("Bad", "X001", 100, "Z")

    def test_returns_validated_item_detail(self):
        """
        The returned ItemDetail must pass its own model_validator —
        i.e. the math is KRA-spec compliant.
        """
        from kra_etims.tax import calculate_item
        from kra_etims.models import ItemDetail
        item = calculate_item("Coffee", "BEV001", Decimal("350"), "A")
        assert isinstance(item, ItemDetail)

    def test_build_invoice_totals(self):
        from kra_etims.tax import calculate_item, build_invoice_totals
        items = [
            calculate_item("A", "I001", 1160, "A"),
            calculate_item("B", "I002", 500, "C"),
        ]
        totals = build_invoice_totals(items)
        assert totals["totItemCnt"] == 2
        assert totals["totAmt"] == Decimal("1660.00")


# ---------------------------------------------------------------------------
# qr.py — QR string extractor
# ---------------------------------------------------------------------------

class TestRenderKraQrString:
    def test_extracts_qr_code_from_top_level(self):
        from kra_etims.qr import render_kra_qr_string
        response = {"qrCode": "20260311T113000;CU12345;INV001;NS;1;4310.34;689.66;5000.00"}
        assert render_kra_qr_string(response).startswith("20260311")

    def test_extracts_qr_code_from_nested_data(self):
        from kra_etims.qr import render_kra_qr_string
        response = {"data": {"qrCode": "some_qr_string"}, "resultCd": "00"}
        assert render_kra_qr_string(response) == "some_qr_string"

    def test_strips_whitespace(self):
        from kra_etims.qr import render_kra_qr_string
        response = {"qrCode": "  qr_data  "}
        assert render_kra_qr_string(response) == "qr_data"

    def test_raises_value_error_when_absent(self):
        from kra_etims.qr import render_kra_qr_string
        with pytest.raises(ValueError, match="No 'qrCode'"):
            render_kra_qr_string({"resultCd": "00", "data": {}})

    def test_generate_qr_bytes_raises_import_error_without_qrcode(self):
        """generate_qr_bytes must provide a clear install instruction."""
        import sys
        # Temporarily hide qrcode from imports
        original_modules = dict(sys.modules)
        sys.modules["qrcode"] = None  # type: ignore[assignment]
        try:
            from kra_etims import qr as qr_module
            import importlib
            importlib.reload(qr_module)
            with pytest.raises(ImportError, match="kra-etims-sdk\\[qr\\]"):
                qr_module.generate_qr_bytes("test")
        finally:
            sys.modules.clear()
            sys.modules.update(original_modules)


# ---------------------------------------------------------------------------
# AsyncKRAeTIMSClient — api_key parity
# ---------------------------------------------------------------------------

class TestAsyncClientApiKeyParity:
    def test_api_key_from_constructor(self):
        from kra_etims.async_client import AsyncKRAeTIMSClient
        client = AsyncKRAeTIMSClient("id", "secret", api_key="test_key")
        assert client._api_key == "test_key"

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("TAXID_API_KEY", "env_key")
        from kra_etims.async_client import AsyncKRAeTIMSClient
        client = AsyncKRAeTIMSClient("id", "secret")
        assert client._api_key == "env_key"

    @pytest.mark.asyncio
    async def test_api_key_skips_oauth(self):
        from kra_etims.async_client import AsyncKRAeTIMSClient
        client = AsyncKRAeTIMSClient("id", "secret", api_key="skip_oauth_key")
        # _authenticate should return immediately without hitting /oauth/token
        with patch.object(client._client, "post") as mock_post:
            await client._authenticate()
            mock_post.assert_not_called()
        await client.aclose()


# ---------------------------------------------------------------------------
# AsyncKRAeTIMSClient — concurrent flush_offline_queue
# ---------------------------------------------------------------------------

class TestAsyncFlushOfflineQueue:
    def _make_invoices(self, n: int):
        from kra_etims.models import SaleInvoice, ItemDetail, TaxType, ReceiptLabel
        from decimal import Decimal
        invoices = []
        for i in range(n):
            item = ItemDetail(
                itemCd=f"ITEM{i:03d}",
                itemNm=f"Product {i}",
                qty=Decimal("1"),
                uprc=Decimal("1160.00"),
                totAmt=Decimal("1160.00"),
                taxTyCd=TaxType.A,
                taxblAmt=Decimal("1000.00"),
                taxAmt=Decimal("160.00"),
            )
            invoices.append(SaleInvoice(
                tin="P051234567X",
                bhfId="00",
                invcNo=f"INV-{i:05d}",
                custNm="Test Customer",
                confirmDt="20260311120000",
                totItemCnt=1,
                totTaxblAmt=Decimal("1000.00"),
                totTaxAmt=Decimal("160.00"),
                totAmt=Decimal("1160.00"),
                itemList=[item],
            ))
        return invoices

    @pytest.mark.asyncio
    async def test_flush_returns_all_results(self):
        from kra_etims.async_client import AsyncKRAeTIMSClient
        client = AsyncKRAeTIMSClient("id", "secret", base_url="http://test.local")
        invoices = self._make_invoices(5)

        async def fake_submit(inv, idempotency_key=None):
            return {"resultCd": "00", "data": {"qrCode": f"qr_{inv.invcNo}"}}

        with patch.object(client, "submit_sale", side_effect=fake_submit):
            results = await client.flush_offline_queue(invoices)

        assert len(results) == 5
        assert all(r["status"] == "success" for r in results)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_flush_isolates_failures(self):
        """A single failed invoice must not abort the rest of the batch."""
        from kra_etims.async_client import AsyncKRAeTIMSClient
        from kra_etims.exceptions import TIaaSUnavailableError
        client = AsyncKRAeTIMSClient("id", "secret", base_url="http://test.local")
        invoices = self._make_invoices(3)
        call_count = 0

        async def fake_submit(inv, idempotency_key=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TIaaSUnavailableError()
            return {"resultCd": "00", "data": {}}

        with patch.object(client, "submit_sale", side_effect=fake_submit):
            results = await client.flush_offline_queue(invoices)

        assert len(results) == 3
        statuses = [r["status"] for r in results]
        assert statuses.count("success") == 2
        assert statuses.count("error") == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_flush_uses_concurrency(self):
        """
        Verify concurrent execution: 20 invoices with a 0.1s delay each
        should complete well under 20 * 0.1s when run concurrently.
        """
        from kra_etims.async_client import AsyncKRAeTIMSClient
        client = AsyncKRAeTIMSClient("id", "secret", base_url="http://test.local")
        invoices = self._make_invoices(20)

        async def slow_submit(inv, idempotency_key=None):
            await asyncio.sleep(0.05)
            return {"resultCd": "00", "data": {}}

        with patch.object(client, "submit_sale", side_effect=slow_submit):
            start = time.monotonic()
            results = await client.flush_offline_queue(invoices)
            elapsed = time.monotonic() - start

        # Sequential would take ≥ 20 * 0.05 = 1.0s
        # Concurrent should finish in ~0.05s + overhead
        assert elapsed < 0.8, f"Flush took {elapsed:.2f}s — expected concurrent execution"
        assert len(results) == 20
        await client.aclose()
