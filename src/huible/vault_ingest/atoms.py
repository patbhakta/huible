"""Vault-shaped atoms and the two-tier storage layout.

Standing rule (HU-1839 doctrine): the vault stores ONLY what an LLM cannot
regenerate — originals, verbatim text, formula LaTeX, table structure, source
images. Everything derived (embeddings, intermediates, approximate chart
values) goes to the TencentDB tier path, never the vault.

On-disk layout under an ingest output root::

    vault/originals/   irreplaceable raw files (pdf/flac/mp4/png), sha256-named
    vault/atoms/       vault-tier atom JSON
    vault/page_png/    page images retained as low-confidence source-of-truth
    derived/atoms/     TencentDB-tier atom JSON (regenerable / approximate)
    derived/media/     regenerable intermediates (16 kHz wav, 1 fps frames)
    manifest.json      run summary
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Tier(StrEnum):
    VAULT = "vault"
    DERIVED = "derived"  # the TencentDB tier


@dataclass
class Atom:
    """One vault-shaped atom with provenance."""

    atom_type: str
    tier: Tier
    source: dict
    provenance: dict
    content: dict

    def to_dict(self) -> dict:
        return {
            "atom_type": self.atom_type,
            "tier": self.tier.value,
            "source": self.source,
            "provenance": self.provenance,
            "content": self.content,
        }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class VaultWriter:
    """Writes atoms + original artifacts into the two-tier layout."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.vault_dir = self.root / Tier.VAULT.value
        self.derived_dir = self.root / Tier.DERIVED.value
        self.originals_dir = self.vault_dir / "originals"
        self.vault_atoms_dir = self.vault_dir / "atoms"
        self.derived_atoms_dir = self.derived_dir / "atoms"
        self.derived_media_dir = self.derived_dir / "media"
        for d in (
            self.originals_dir,
            self.vault_atoms_dir,
            self.derived_atoms_dir,
            self.derived_media_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.atoms: list[dict] = []

    def store_original(self, path: Path) -> dict:
        """Copy an irreplaceable raw file into the vault, sha256-named."""
        path = Path(path)
        digest = sha256_file(path)
        dest = self.originals_dir / f"{digest}{path.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(path, dest)
        return {
            "file": str(path),
            "stored_as": str(dest.relative_to(self.root)),
            "sha256": digest,
            "bytes": dest.stat().st_size,
        }

    def write_atom(self, atom: Atom, slug: str | None = None) -> Path:
        payload = atom.to_dict()
        atoms_dir = self.vault_atoms_dir if atom.tier is Tier.VAULT else self.derived_atoms_dir
        stem = slug or (Path(str(atom.source.get("file", "input"))).stem or "input")
        name = f"{stem}.{atom.atom_type}.{atom.tier.value}.json"
        out = atoms_dir / name
        out.write_text(json.dumps(payload, indent=2))
        self.atoms.append(payload)
        return out

    def write_manifest(self, extra: dict | None = None) -> Path:
        manifest = {
            "root": str(self.root),
            "atom_count": len(self.atoms),
            "vault_atoms": sum(1 for a in self.atoms if a["tier"] == Tier.VAULT.value),
            "derived_atoms": sum(1 for a in self.atoms if a["tier"] == Tier.DERIVED.value),
            "atoms": self.atoms,
        }
        if extra:
            manifest.update(extra)
        out = self.root / "manifest.json"
        out.write_text(json.dumps(manifest, indent=2))
        return out


def atom_from(
    atom_type: str,
    tier: Tier,
    source: dict,
    provenance: dict,
    content: dict,
) -> Atom:
    return Atom(
        atom_type=atom_type,
        tier=tier,
        source=source,
        provenance=provenance,
        content=content,
    )
