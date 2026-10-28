"""Pull a container image (linux/amd64) from a registry and merge its layers into one root file
system tar for `wsl --import`: Home Assistant's own image on Windows without Docker.

    uv run --python 3.14 scripts/dev/oci_rootfs.py ghcr.io home-assistant/home-assistant dev \
        .dev/rootfs.tar .dev/layers

argv: registry, repository, tag, output tar, folder for the downloaded layers.
"""

import gzip
import json
import shutil
import sys
import tarfile
import urllib.request
from compression import zstd
from pathlib import Path

registry, repository, tag, out, work = sys.argv[1:6]
work_dir = Path(work)
work_dir.mkdir(parents=True, exist_ok=True)


def get(url: str, token: str, accept: str) -> urllib.request.addinfourl:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": accept}
    )
    return urllib.request.urlopen(request, timeout=600)


with urllib.request.urlopen(
    f"https://{registry}/token?scope=repository:{repository}:pull", timeout=60
) as response:
    token = json.load(response)["token"]

index_types = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
with get(f"https://{registry}/v2/{repository}/manifests/{tag}", token, index_types) as r:
    index = json.load(r)
if "manifests" in index:
    chosen = next(
        m
        for m in index["manifests"]
        if m.get("platform", {}).get("os") == "linux"
        and m["platform"].get("architecture") == "amd64"
    )
    with get(
        f"https://{registry}/v2/{repository}/manifests/{chosen['digest']}", token, index_types
    ) as r:
        manifest = json.load(r)
else:
    manifest = index

layers: list[Path] = []
for number, layer in enumerate(manifest["layers"]):
    target = work_dir / f"layer{number:02d}.tar"
    layers.append(target)
    if target.exists():
        continue
    print(f"layer {number + 1}/{len(manifest['layers'])}: {layer['size'] / 1e6:.0f} MB", flush=True)
    with get(f"https://{registry}/v2/{repository}/blobs/{layer['digest']}", token, "*/*") as r:
        partial = target.with_suffix(".part")
        zstd_layer = "zstd" in layer["mediaType"]
        unzipped_file = zstd.ZstdFile(r) if zstd_layer else gzip.GzipFile(fileobj=r)
        with unzipped_file as unzipped, open(partial, "wb") as file:
            shutil.copyfileobj(unzipped, file, 1 << 20)
        partial.rename(target)

# Which layer has the last word on each path, with whiteouts applied.
winner: dict[str, int] = {}
for number, path in enumerate(layers):
    with tarfile.open(path) as tar:
        for member in tar:
            name = member.name.lstrip("./").rstrip("/")
            parent, _, base = name.rpartition("/")
            if base == ".wh..wh..opq":
                prefix = f"{parent}/" if parent else ""
                for key in [k for k in winner if k.startswith(prefix) and k != parent]:
                    del winner[key]
                continue
            if base.startswith(".wh."):
                gone = f"{parent}/{base[4:]}" if parent else base[4:]
                for key in [k for k in winner if k == gone or k.startswith(gone + "/")]:
                    del winner[key]
                continue
            if name in winner and not member.isdir():
                # A file replacing a directory drops what was below it.
                for key in [k for k in winner if k.startswith(name + "/")]:
                    del winner[key]
            winner[name] = number

with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as merged:
    for number, path in enumerate(layers):
        with tarfile.open(path) as tar:
            for member in tar:
                name = member.name.lstrip("./").rstrip("/")
                if winner.get(name) != number:
                    continue
                member.name = name
                merged.addfile(member, tar.extractfile(member) if member.isfile() else None)
with get(
    f"https://{registry}/v2/{repository}/blobs/{manifest['config']['digest']}", token, "*/*"
) as r:
    (work_dir / "config.json").write_bytes(r.read())
print("done", out, flush=True)
