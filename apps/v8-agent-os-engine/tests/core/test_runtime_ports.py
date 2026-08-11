from core import system_doctor
from core.runtime_ports import DEFAULT_WEB_PORT, governed_web_origins, governed_web_port


def test_governed_web_port_accepts_only_the_default_or_managed_loopback_range(monkeypatch):
    monkeypatch.setenv("V8_WEB_BASE_URL", "http://127.0.0.1:19527")
    assert governed_web_port() == 19527
    assert governed_web_origins() == ["http://127.0.0.1:19527", "http://localhost:19527"]
    assert governed_web_port("http://localhost:19546/chat") == 19546
    assert governed_web_port("http://[::1]:9527") == 9527


def test_governed_web_port_rejects_external_or_ungoverned_urls():
    for candidate in (
        "https://127.0.0.1:19527",
        "http://example.com:19527",
        "http://user:password@127.0.0.1:19527",
        "http://127.0.0.1:19547",
        "not-a-url",
    ):
        assert governed_web_port(candidate) == DEFAULT_WEB_PORT


def test_system_doctor_checks_the_governed_web_fallback(monkeypatch):
    probed_ports = []

    def fake_connect(port):
        probed_ports.append(port)
        return {"host": "127.0.0.1", "port": port, "open": True}

    monkeypatch.setattr(system_doctor, "governed_web_port", lambda: 19527)
    monkeypatch.setattr(system_doctor, "_connect_port", fake_connect)
    checks = system_doctor.SystemDoctorService()._check_ports()
    assert probed_ports == [9530, 9528, 19527]
    assert checks[-1]["id"] == "ports.19527"
    assert checks[-1]["title"] == "Web port 19527"
