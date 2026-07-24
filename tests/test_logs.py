import pytest

from web.app import app, read_log_tail


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = tmp_path
    app.config["SECRET_KEY"] = "test-secret"

    with app.test_client() as c:
        yield c


# --- read_log_tail() ---

def test_read_log_tail_gibt_letzte_n_zeilen(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("\n".join(f"Zeile {i}" for i in range(1, 251)) + "\n")
    result = read_log_tail(log, n=200)
    assert len(result) == 200
    assert result[0] == "Zeile 51"
    assert result[-1] == "Zeile 250"


def test_read_log_tail_weniger_zeilen_als_limit(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("Zeile 1\nZeile 2\nZeile 3\n")
    result = read_log_tail(log, n=200)
    assert result == ["Zeile 1", "Zeile 2", "Zeile 3"]


def test_read_log_tail_fehlende_datei(tmp_path):
    log = tmp_path / "nicht_vorhanden.log"
    assert read_log_tail(log, n=200) == []


def test_read_log_tail_leere_datei(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("")
    assert read_log_tail(log, n=200) == []


# --- /logs Route ---

def test_logs_erreichbar(client):
    response = client.get("/logs")
    assert response.status_code == 200


def test_logs_leerzustand_bei_fehlender_datei(client):
    html = client.get("/logs").data.decode()
    assert "Noch keine Log-Einträge vorhanden" in html


def test_logs_leerzustand_bei_leerer_datei(client, tmp_path):
    (tmp_path / "app.log").write_text("")
    html = client.get("/logs").data.decode()
    assert "Noch keine Log-Einträge vorhanden" in html


def test_logs_zeigt_neueste_zuerst(client, tmp_path):
    (tmp_path / "app.log").write_text("Zeile 1\nZeile 2\nZeile 3\n")
    html = client.get("/logs").data.decode()
    pos1 = html.index("Zeile 1")
    pos2 = html.index("Zeile 2")
    pos3 = html.index("Zeile 3")
    assert pos3 < pos2 < pos1


def test_logs_tail_limit_200_zeilen(client, tmp_path):
    (tmp_path / "app.log").write_text("\n".join(f"Zeile {i}" for i in range(1, 251)) + "\n")
    html = client.get("/logs").data.decode()
    assert "Zeile 250" in html
    assert "Zeile 51" in html
    assert "Zeile 50" not in html


# --- Navigation ---

def test_index_hat_link_zu_logs(client):
    html = client.get("/").data.decode()
    assert "/logs" in html
