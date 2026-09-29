"""Download the exact reviewed TestPyPI archives for public promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def download_release(
    version: str, wheel_sha256: str, sdist_sha256: str, destination: Path
) -> None:
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]*", version) is None:
        raise ValueError("invalid release version")
    expected = {
        f"tokenhub-{version}-py3-none-any.whl": wheel_sha256.lower(),
        f"tokenhub-{version}.tar.gz": sdist_sha256.lower(),
    }
    if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in expected.values()):
        raise ValueError("both reviewed SHA-256 hashes are required")

    url = f"https://test.pypi.org/pypi/tokenhub/{urllib.parse.quote(version)}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        release = json.load(response)
    files = {item["filename"]: item for item in release["urls"]}
    if files.keys() != expected.keys():
        raise ValueError("TestPyPI release does not contain exactly the expected archives")

    destination.mkdir(parents=True, exist_ok=True)
    for filename, digest in expected.items():
        item = files[filename]
        if item["digests"]["sha256"] != digest:
            raise ValueError(f"reviewed SHA-256 does not match TestPyPI: {filename}")
        with urllib.request.urlopen(item["url"], timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"downloaded SHA-256 does not match review: {filename}")
        (destination / filename).write_bytes(content)
        print(f"promoting {filename}: {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("wheel_sha256")
    parser.add_argument("sdist_sha256")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    download_release(
        args.version, args.wheel_sha256, args.sdist_sha256, args.destination
    )


if __name__ == "__main__":
    main()
