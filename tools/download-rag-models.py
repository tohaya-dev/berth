#!/usr/bin/env python3
"""Fetch only pinned RAG models and verify every expected file."""
import argparse,hashlib,json
from pathlib import Path
from huggingface_hub import snapshot_download
p=argparse.ArgumentParser();p.add_argument("--cache",type=Path,required=True);a=p.parse_args()
for model in json.loads((Path(__file__).resolve().parents[1]/"locks/models.json").read_text()):
    location=Path(snapshot_download(model["model"],revision=model["revision"],cache_dir=str(a.cache),
        allow_patterns=[f["file"] for f in model["files"]]))
    for file in model["files"]:
        path=location/file["file"];h=hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""):h.update(block)
        if path.stat().st_size!=file["bytes"] or h.hexdigest()!=file["sha256"]:
            raise SystemExit("FAIL: model checksum mismatch: "+file["file"])
    refs=location.parent.parent/"refs";refs.mkdir(exist_ok=True)
    (refs/"main").write_text(model["revision"])
    print(model["model"]+" pinned files verified")
print("No generative model downloaded")
