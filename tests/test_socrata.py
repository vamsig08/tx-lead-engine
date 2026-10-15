import httpx
import respx

from integrations.socrata import SocrataClient
from tests.conftest import load_fixture

BASE = "https://data.texas.gov"


@respx.mock
def test_query_pages_until_a_short_page():
    rows = load_fixture("socrata_sales_tax.json")
    route = respx.get(f"{BASE}/resource/jrea-zgmq.json").mock(side_effect=[
        httpx.Response(200, json=rows[:2]),
        httpx.Response(200, json=rows[2:4]),
        httpx.Response(200, json=[]),
    ])
    client = SocrataClient(BASE, page_size=2)
    result = list(client.query("jrea-zgmq", "outlet_county_code IN ('057')"))
    client.close()

    assert result == rows
    offsets = [call.request.url.params["$offset"] for call in route.calls]
    assert offsets == ["0", "2", "4"]
    first = route.calls[0].request.url.params
    assert first["$where"] == "outlet_county_code IN ('057')"
    assert first["$order"] == ":id"
    assert first["$limit"] == "2"
