async def test_thin_hello(harness):
    c = await harness.connect()
    w = await c.hello()
    assert w["resumed"] is False and w["principal"]["kind"] == "local"
    await c.expect(type="menu")
    v = await c.expect(type="view")
    assert v["screen"] == "home"
    v = await c.input("down")
    assert v["screen"] == "menu"
