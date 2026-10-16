from tools import get_clinic_info


async def test_general(db):
    pool, _, _ = db
    result = await get_clinic_info.run(pool, "call-1", {"topic": "general"})
    assert result["status"] == "ok"
    assert len(result["data"]["doctors"]) == 2


async def test_doctors_topic(db):
    pool, _, _ = db
    result = await get_clinic_info.run(pool, "call-1", {"topic": "doctors"})
    assert "doctors" in result["data"]


async def test_fees_topic(db):
    pool, _, _ = db
    result = await get_clinic_info.run(pool, "call-1", {"topic": "fees"})
    assert "fees" in result["data"]
    assert result["data"]["fees"][0]["consultation_fee"] is not None


async def test_hours_topic(db):
    pool, _, _ = db
    result = await get_clinic_info.run(pool, "call-1", {"topic": "hours"})
    assert "hours" in result["data"]
