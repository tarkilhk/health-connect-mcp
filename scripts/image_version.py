"""Allocate the next image patch version from the tags already published to GHCR."""

import base64
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

NUMBER = r"(?:0|[1-9][0-9]*)"


def next_version(series: str, tags: list[str]) -> str:
    """Start a new major.minor series at zero; increment its highest published patch."""
    if not re.fullmatch(rf"{NUMBER}\.{NUMBER}", series):
        raise ValueError("VERSION must contain major.minor, for example 0.2")
    pattern = re.compile(rf"{re.escape(series)}\.({NUMBER})")
    patches = [int(match[1]) for tag in tags if (match := pattern.fullmatch(tag))]
    return f"{series}.{max(patches, default=-1) + 1}"


def registry_tags(repository: str, username: str, password: str) -> list[str]:
    """Read all tag pages; only a missing image is treated as an empty series."""
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull,push"})
    request = Request(f"https://ghcr.io/token?{query}", headers={
        "Authorization": f"Basic {credentials}",
    })
    with urlopen(request, timeout=30) as response:
        token = json.load(response)["token"]

    tags = []
    path = f"/v2/{repository}/tags/list"
    url = f"https://ghcr.io{path}?n=100"
    first_page = True
    while url:
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urlopen(request, timeout=30) as response:
                tags.extend(json.load(response)["tags"] or [])
                link = response.headers.get("Link", "")
        except HTTPError as error:
            if error.code == 404 and first_page:
                return []
            raise
        first_page = False
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = urljoin(url, match[1]) if match else ""
        if url:
            target = urlsplit(url)
            if (target.scheme, target.netloc, target.path) != ("https", "ghcr.io", path):
                raise ValueError("Unexpected GHCR pagination URL")
    return tags


def main() -> None:
    series = Path("VERSION").read_text().strip()
    next_version(series, [])  # Validate before making authenticated requests.
    repository = os.environ["GITHUB_REPOSITORY"].lower()
    tags = registry_tags(repository, os.environ["GITHUB_ACTOR"], os.environ["GHCR_TOKEN"])
    version = next_version(series, tags)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"version={version}\n")
    print(f"Publishing ghcr.io/{repository}:{version}")


if __name__ == "__main__":
    main()
