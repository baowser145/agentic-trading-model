from stock_screener_30d.activity_log import clear_logs, get_logs, log


def test_activity_log():
    clear_logs()
    id1 = log("hello", source="test")
    id2 = log("world", level="success", source="test")
    logs = get_logs()
    assert len(logs) >= 2
    assert logs[-2]["id"] == id1
    assert logs[-1]["message"] == "world"
    since = get_logs(since_id=id1)
    assert len(since) == 1
    assert since[0]["id"] == id2