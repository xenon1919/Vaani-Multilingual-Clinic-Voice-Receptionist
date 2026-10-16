from tools import transfer_to_human


async def test_emergency_message():
    result = await transfer_to_human.run(None, "call-1", {"reason": "emergency"})
    assert result["status"] == "ok"
    assert "emergency" in result["message"].lower()


async def test_caller_request_message():
    result = await transfer_to_human.run(None, "call-1", {"reason": "caller_request"})
    assert result["status"] == "ok"


async def test_default_reason_is_caller_request():
    result = await transfer_to_human.run(None, "call-1", {})
    assert result["reason"] == "caller_request"
