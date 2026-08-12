import httpx
import pytest

from extractor.reference import load_gics_map, parse_gics_table

CONSTITUENTS_HTML = """
<html><body>
<table id="constituents" class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
    <th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td>MMM</td><td>3M</td><td>Industrials</td><td>Industrial Conglomerates</td>
    <td>Saint Paul, Minnesota</td><td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td>AOS</td><td>A. O. Smith</td><td>Industrials</td><td>Building Products</td>
    <td>Milwaukee, Wisconsin</td><td>2017-07-26</td><td>0000091142</td><td>1916</td></tr>
<tr><td>ABT</td><td>Abbott Laboratories</td><td>Health Care</td><td>Health Care Equipment</td>
    <td>North Chicago, Illinois</td><td>1957-03-04</td><td>0000001800</td><td>1888</td></tr>
</table>
</body></html>
"""

NO_TABLE_HTML = "<html><body><p>Page structure changed, no table here.</p></body></html>"


def test_parse_gics_table_maps_ticker_to_sector_and_sub_industry():
    result = parse_gics_table(CONSTITUENTS_HTML)

    assert result["MMM"] == {"sector": "Industrials", "sub_industry": "Industrial Conglomerates"}
    assert result["ABT"] == {"sector": "Health Care", "sub_industry": "Health Care Equipment"}
    assert len(result) == 3


def test_parse_gics_table_raises_value_error_when_constituents_table_missing():
    with pytest.raises(ValueError):
        parse_gics_table(NO_TABLE_HTML)


async def test_load_gics_map_returns_parsed_map_on_success():
    def handler(request):
        return httpx.Response(200, text=CONSTITUENTS_HTML)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await load_gics_map(client)

    assert result["MMM"] == {"sector": "Industrials", "sub_industry": "Industrial Conglomerates"}


async def test_load_gics_map_returns_empty_dict_on_network_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.warns(UserWarning):
            result = await load_gics_map(client)

    assert result == {}


async def test_load_gics_map_returns_empty_dict_when_table_missing():
    def handler(request):
        return httpx.Response(200, text=NO_TABLE_HTML)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.warns(UserWarning):
            result = await load_gics_map(client)

    assert result == {}
