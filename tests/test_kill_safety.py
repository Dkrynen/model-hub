import server


def test_clear_port_refuses_foreign_process(monkeypatch):
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["9999"])
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: False)
    killed = []
    monkeypatch.setattr(server, "kill_pids", lambda pids: killed.extend(pids) or killed)
    ok = server.clear_port(5050, force=True)
    assert killed == []        # never touched the foreign process
    assert ok is False         # refused, honest failure


def test_clear_port_kills_our_stale_lac(monkeypatch):
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["1234"])
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: True)
    monkeypatch.setattr(server, "kill_pids", lambda pids: pids)
    monkeypatch.setattr(server, "find_port_pids", lambda port: ["1234"])
    # after "kill", pretend the port frees:
    calls = {"n": 0}

    def _pids(port):
        calls["n"] += 1
        return ["1234"] if calls["n"] == 1 else []
    monkeypatch.setattr(server, "find_port_pids", _pids)
    ok = server.clear_port(5050, force=True)
    assert ok is True


def test_kill_pids_filters_to_ours(monkeypatch):
    monkeypatch.setattr(server, "_process_is_ours", lambda pid: pid == "111")
    ran = []
    monkeypatch.setattr(server.proc, "run", lambda *a, **k: ran.append(a) or None)
    monkeypatch.setattr(server.os, "name", "nt")
    killed = server.kill_pids(["111", "222"])
    assert killed == ["111"]   # 222 was foreign → never killed


def test_process_is_ours_true_for_registry(monkeypatch):
    server.proc.register_spawned(555)
    assert server._process_is_ours("555") is True


def test_process_is_ours_true_for_real_lac_tasklist(monkeypatch):
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server.proc, "is_ours", lambda pid: False)
    class R:
        stdout = '"lac.exe","1234","Console","1","5,000 K"\n'
    monkeypatch.setattr(server.proc, "run", lambda *a, **k: R())
    assert server._process_is_ours("1234") is True


def test_process_is_ours_false_for_decoy_name(monkeypatch):
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server.proc, "is_ours", lambda pid: False)
    class R:
        stdout = '"notlac.exe","1234","Console","1","5,000 K"\n'
    monkeypatch.setattr(server.proc, "run", lambda *a, **k: R())
    assert server._process_is_ours("1234") is False
