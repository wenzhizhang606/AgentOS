"""AC-013. The public home and its illustration work before a project is selected."""

from importlib.resources import files
from unittest.mock import Mock

from fastapi.testclient import TestClient

from agentos.api.app import TaskStore, create_app
from agentos.governance.auth import Authenticator


def test_ac013_public_root_redirects_to_uncached_home():
    store = Mock(spec=TaskStore)
    with TestClient(create_app(store, Authenticator({}), "release")) as client:
        for method in ("GET", "HEAD"):
            redirect = client.request(method, "/", follow_redirects=False)
            assert redirect.status_code == 302
            assert redirect.headers["location"] == "/console"
            home = client.request(method, "/console")
            assert home.status_code == 200
            assert home.headers["cache-control"] == "no-store"
            assert "img-src 'self'" in home.headers["content-security-policy"]
    assert not store.mock_calls


def test_ac013_public_image_has_conditional_cache_and_exact_path():
    store = Mock(spec=TaskStore)
    image_url = "/console/assets/architecture.webp"
    packaged = files("agentos.observability").joinpath("architecture.webp").read_bytes()
    assert packaged[:4] == b"RIFF" and packaged[8:12] == b"WEBP"
    with TestClient(create_app(store, Authenticator({}), "release")) as client:
        image = client.get(image_url)
        assert image.status_code == 200
        assert image.content == packaged
        assert image.headers["content-type"] == "image/webp"
        assert image.headers["cache-control"] == "public, max-age=3600"
        assert image.headers["content-length"] == str(len(packaged))
        info = client.head(image_url)
        assert info.status_code == 200 and info.content == b""
        for name in ("etag", "content-length", "cache-control", "content-type"):
            assert info.headers[name] == image.headers[name]
        cached = client.get(image_url, headers={"If-None-Match": image.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["cache-control"] == image.headers["cache-control"]
        unknown = client.get("/console/assets/page.py")
        assert unknown.status_code == 404 and unknown.headers["cache-control"] == "no-store"
        assert client.post(image_url).status_code == 405
        assert client.get("/v1/projects/sample/resources").status_code == 401
    assert not store.mock_calls
