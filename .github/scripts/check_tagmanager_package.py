"""Fail closed if the feed advertises other plugins or ships unsafe/local files."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

import yaml


def check(directory):
    directory = Path(directory)
    assert {p.name for p in directory.iterdir()} == {"index.yml", "tagManager.zip"}
    index = yaml.safe_load((directory / "index.yml").read_text())
    assert len(index) == 1 and index[0]["id"] == "tagManager"
    assert index[0]["path"] == "tagManager.zip"
    package = directory / "tagManager.zip"
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert index[0]["sha256"] == digest
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    license_bytes = subprocess.check_output(["git", "show", "HEAD:LICENSE"])
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert names == sorted(names) and len(names) == len(set(names))
        assert {"tagManager.yml", "tag_manager.py", "stashdb_api.py", "sync_history.py", "LICENSE"} <= set(names)
        assert archive.read("LICENSE") == license_bytes
        manifest = yaml.safe_load(archive.read("tagManager.yml"))
        assert index[0]["version"].startswith(manifest["version"] + "-")
        assert manifest["exec"] == ["python", "{pluginDir}/tag_manager.py"]
        for name in names:
            parts = PurePosixPath(name).parts
            assert not name.startswith("/") and ".." not in parts
            assert not {"tests", "samples", "__pycache__", "data", "cache", "plugin_data"} & set(parts)
            assert not any(p.startswith((".env", "test_")) or p == "conftest.py" for p in parts)
            source_path = "LICENSE" if name == "LICENSE" else "plugins/tagManager/" + name.removeprefix("./")
            assert archive.read(name) == subprocess.check_output(["git", "show", "HEAD:" + source_path])
    print(json.dumps({"source_commit": source, "version": index[0]["version"],
                      "zip_sha256": digest, "files": names}, indent=2))


if __name__ == "__main__":
    check(sys.argv[1])
