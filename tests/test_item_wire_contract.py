"""
Wire-contract tests for item registration (Category 4).

Prior to v0.4.1 ``save_item`` posted the KRA-native ``ItemSave`` dump to
``/v2/etims/item`` (singular) — the middleware's endpoint is
``POST /v2/etims/items`` and it expects the item-registry schema
(``sku``/``itemNm``/``itemClsCd``/``taxTyCd``/``qty``/``unitPrice``).
Same class of drift fixed for ``submit_sale`` in v0.4.0.
"""
from decimal import Decimal

from kra_etims.client import KRAeTIMSClient
from kra_etims.models import ItemSave, ItemType, TaxType, to_middleware_item_payload


def _fuel_item(**overrides):
    kwargs = dict(
        tin="A008697103A",
        bhfId="00",
        itemCd="PMS-SUPER",
        itemClsCd="15101506",
        itemNm="Super Petrol (PMS)",
        itemTyCd=ItemType.GOODS,
        taxTyCd=TaxType.E,
        uprc=Decimal("214.03"),
        qtyUnitCd="LTR",
    )
    kwargs.update(overrides)
    return ItemSave(**kwargs)


def test_item_payload_maps_to_middleware_registry_schema():
    payload = to_middleware_item_payload(_fuel_item())
    assert payload == {
        "sku":       "PMS-SUPER",
        "itemNm":    "Super Petrol (PMS)",
        "itemClsCd": "15101506",
        "taxTyCd":   "E",
        "qty":       "1",
        "unitPrice": "214.03",
        "qtyUnitCd": "LTR",
    }
    # KRA-native envelope fields must NOT leak onto the wire — the middleware
    # derives tin/bhfId from the API key and generates itemCd server-side.
    for forbidden in ("tin", "bhfId", "itemTyCd", "uprc", "isUsed"):
        assert forbidden not in payload


def test_save_item_posts_plural_items_endpoint(httpx_mock):
    client = KRAeTIMSClient("id", "secret", base_url="https://api.test")
    client._access_token = "mock"
    client._token_expiry = 9999999999

    httpx_mock.add_response(
        method="POST",
        url="https://api.test/v2/etims/items",
        json={"sku": "PMS-SUPER", "itemCd": "ABC", "vscuRegistered": True},
        status_code=200,
    )

    result = client.save_item(_fuel_item())
    assert result["vscuRegistered"] is True

    sent = httpx_mock.get_requests()[0]
    assert sent.url.path == "/v2/etims/items"
