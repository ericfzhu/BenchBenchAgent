"""Build digest-bound dependency environments from local approved wheels."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from bba.evidence import file_digest, tree_digest
from bba.protocol import digest_json


LOCK_PATTERN = re.compile(
    r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+) --hash=sha256:([0-9a-f]{64})$"
)


@dataclass(frozen=True)
class DependencyEnvironment:
    python: str
    site_packages: str | None
    lock_digest: str
    catalog_digest: str
    environment_digest: str


class LocalWheelCatalog:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.catalog_path = self.root / "catalog.json"
        if not self.catalog_path.is_file():
            raise FileNotFoundError(f"local wheel catalog does not exist: {self.catalog_path}")
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1 or not isinstance(value.get("wheels"), list):
            raise ValueError("local wheel catalog has an invalid schema")
        self.value = value
        self.entries = {
            (item["name"].lower().replace("_", "-"), item["version"]): item
            for item in value["wheels"]
        }
        if len(self.entries) != len(value["wheels"]):
            raise ValueError("local wheel catalog has duplicate package versions")

    @property
    def digest(self) -> str:
        return digest_json(self.value)

    def resolve(self, name: str, version: str, digest: str) -> Path:
        entry = self.entries.get((name.lower().replace("_", "-"), version))
        if entry is None or entry.get("sha256") != digest:
            raise ValueError(f"dependency is not in the approved wheel catalog: {name}=={version}")
        filename = str(entry.get("filename", ""))
        if not filename.endswith(".whl") or Path(filename).name != filename:
            raise ValueError("approved dependency must be one local wheel file")
        wheel = self.root / filename
        if not wheel.is_file() or file_digest(wheel) != digest:
            raise ValueError(f"approved wheel digest is invalid: {filename}")
        return wheel


def parse_lockfile(path: Path) -> Tuple[Tuple[str, str, str], ...]:
    rows = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCK_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(
                f"requirements.lock line {number} must use NAME==VERSION --hash=sha256:DIGEST"
            )
        rows.append(match.groups())
    if len({name.lower().replace("_", "-") for name, _version, _digest in rows}) != len(rows):
        raise ValueError("requirements.lock contains a duplicate package")
    return tuple(rows)


def build_dependency_environment(
    lockfile: Path,
    catalog: LocalWheelCatalog,
    output_root: Path,
) -> DependencyEnvironment:
    lockfile = Path(lockfile).resolve()
    requirements = parse_lockfile(lockfile)
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    lock_digest = file_digest(lockfile)
    identity = digest_json({
        "lock_digest": lock_digest,
        "catalog_digest": catalog.digest,
        "python": sys.version,
    })
    environment_root = output_root / identity
    site = environment_root / "site-packages"
    if requirements and not site.is_dir():
        wheels = [catalog.resolve(*row) for row in requirements]
        temporary = output_root / f".{identity}.building"
        if temporary.exists():
            raise RuntimeError("dependency environment has an incomplete prior build")
        temporary.mkdir()
        temporary_site = temporary / "site-packages"
        temporary_site.mkdir()
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--only-binary=:all:",
            "--no-deps",
            "--target",
            str(temporary_site),
            *[str(item) for item in wheels],
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-2000:] or "offline wheel installation failed")
        temporary.rename(environment_root)
    environment_digest = digest_json({
        "identity": identity,
        "installed_tree": tree_digest(site) if site.is_dir() else None,
    })
    return DependencyEnvironment(
        python=str(Path(sys.executable).resolve()),
        site_packages=str(site) if requirements else None,
        lock_digest=lock_digest,
        catalog_digest=catalog.digest,
        environment_digest=environment_digest,
    )
