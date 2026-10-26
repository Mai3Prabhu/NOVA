from urllib.parse import quote

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)

VALID_ID = "d41d8cd98f00b204e9800998ecf8427e"


def _write_fake_docx(tmp_path, document_id: str) -> None:
    (tmp_path / f"{document_id}.docx").write_bytes(b"not a real docx, just bytes")


def test_download_returns_the_file_for_a_valid_document_id(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path)
    _write_fake_docx(tmp_path, VALID_ID)

    response = client.get(f"/download/{VALID_ID}")

    assert response.status_code == 200
    assert response.content == b"not a real docx, just bytes"
    assert "wordprocessingml" in response.headers["content-type"]


def test_download_returns_404_for_unknown_document_id(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path)
    # Valid-looking id, but no such file was ever created.

    response = client.get(f"/download/{VALID_ID}")

    assert response.status_code == 404


def test_download_rejects_path_traversal_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path)
    # A real secret file sitting just outside GENERATED_DIR.
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")

    traversal_id = "../" + secret.name
    response = client.get(f"/download/{quote(traversal_id, safe='')}")

    assert response.status_code == 404


def test_download_rejects_non_hex_document_id(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "GENERATED_DIR", tmp_path)

    response = client.get("/download/not-a-valid-id")

    assert response.status_code == 404
