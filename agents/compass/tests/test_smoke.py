import mde_compass


def test_package_identity() -> None:
    assert mde_compass.AGENT_ID == "compass"
    assert mde_compass.CALL_PREFIX == "cmp."
    assert mde_compass.DEFAULT_PORT == 8110
