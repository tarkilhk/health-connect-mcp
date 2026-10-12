"""Image versions advance from published GHCR tags without hiding registry errors."""

import base64
import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import image_version


@pytest.mark.parametrize(("series", "tags", "expected"), [
    ("0.2", [], "0.2.0"),
    ("0.2", ["0.2.0"], "0.2.1"),
    ("0.2", ["0.2.9", "0.2.2", "0.2.10"], "0.2.11"),
    ("0.3", ["0.2.99"], "0.3.0"),
    ("1.0", ["0.9.99", "1.0.0"], "1.0.1"),
    ("0.2", ["latest", "0.20.3", "0.2.01", "0.2.4-rc.1"], "0.2.0"),
])
def test_next_version(series, tags, expected):
    assert image_version.next_version(series, tags) == expected


@pytest.mark.parametrize("series", ["", "0", "v0.2", "0.2.0", "00.2", "0.02", "-1.0"])
def test_invalid_series(series):
    with pytest.raises(ValueError, match="major.minor"):
        image_version.next_version(series, [])


class Response(io.BytesIO):
    def __init__(self, data, link=""):
        super().__init__(json.dumps(data).encode())
        self.headers = {"Link": link}


def registry_responses(monkeypatch, responses):
    requests = []
    responses = iter(responses)

    def open_response(request, timeout):
        assert timeout == 30
        requests.append(request)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(image_version, "urlopen", open_response)
    return requests


def test_private_registry_auth_and_pagination(monkeypatch):
    requests = registry_responses(monkeypatch, [
        Response({"token": "registry-token"}),
        Response({"tags": ["0.2.0"]},
                 '</v2/owner/app/tags/list?n=100&last=0.2.0>; rel="next"'),
        Response({"tags": ["0.2.10", "latest"]}),
    ])
    tags = image_version.registry_tags("owner/app", "actor", "github-token")
    assert image_version.next_version("0.2", tags) == "0.2.11"
    auth = requests[0].get_header("Authorization").removeprefix("Basic ")
    assert base64.b64decode(auth).decode() == "actor:github-token"
    query = parse_qs(urlsplit(requests[0].full_url).query)
    assert query == {"service": ["ghcr.io"], "scope": ["repository:owner/app:pull,push"]}
    assert requests[1].get_header("Authorization") == "Bearer registry-token"
    assert requests[2].full_url.endswith("?n=100&last=0.2.0")
    assert requests[2].get_header("Authorization") == "Bearer registry-token"


def test_first_publish_starts_at_patch_zero(monkeypatch):
    registry_responses(monkeypatch, [
        Response({"token": "registry-token"}),
        HTTPError("https://ghcr.io/v2/owner/app/tags/list", 404, "missing", {}, None),
    ])
    tags = image_version.registry_tags("owner/app", "actor", "github-token")
    assert image_version.next_version("0.2", tags) == "0.2.0"


def test_empty_registry(monkeypatch):
    registry_responses(monkeypatch, [
        Response({"token": "registry-token"}), Response({"tags": None}),
    ])
    assert image_version.registry_tags("owner/app", "actor", "github-token") == []


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_registry_errors_stop_publication(monkeypatch, status):
    registry_responses(monkeypatch, [
        Response({"token": "registry-token"}),
        HTTPError("https://ghcr.io/v2/owner/app/tags/list", status, "failed", {}, None),
    ])
    with pytest.raises(HTTPError) as error:
        image_version.registry_tags("owner/app", "actor", "github-token")
    assert error.value.code == status


def test_missing_later_page_is_an_error(monkeypatch):
    registry_responses(monkeypatch, [
        Response({"token": "registry-token"}),
        Response({"tags": ["0.2.0"]},
                 '</v2/owner/app/tags/list?n=100&last=0.2.0>; rel="next"'),
        HTTPError("https://ghcr.io/v2/owner/app/tags/list", 404, "missing", {}, None),
    ])
    with pytest.raises(HTTPError):
        image_version.registry_tags("owner/app", "actor", "github-token")


def test_pagination_cannot_send_credentials_to_another_host(monkeypatch):
    requests = registry_responses(monkeypatch, [
        Response({"token": "registry-token"}),
        Response({"tags": ["0.2.0"]}, '<https://example.com/tags>; rel="next"'),
    ])
    with pytest.raises(ValueError, match="pagination URL"):
        image_version.registry_tags("owner/app", "actor", "github-token")
    assert len(requests) == 2


def test_workflow_output_uses_version_file_and_published_tags(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "VERSION").write_text("0.2\n")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_REPOSITORY", "Owner/App")
    monkeypatch.setenv("GITHUB_ACTOR", "actor")
    monkeypatch.setenv("GHCR_TOKEN", "github-token")
    registry_responses(monkeypatch, [
        Response({"token": "registry-token"}), Response({"tags": ["0.2.7"]}),
    ])
    image_version.main()
    assert output.read_text() == "version=0.2.8\n"
