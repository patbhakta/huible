from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from huible.memory.protocol import MemoryNode, MemoryTier


@dataclass(slots=True, frozen=True)
class VersionEntry:
    version: int
    memory: MemoryNode
    is_active: bool


@dataclass(slots=True, frozen=True)
class VersionChain:
    memory_id: UUID
    persona_id: UUID
    tier: MemoryTier
    content_type: str
    versions: list[VersionEntry] = field(default_factory=list)

    @property
    def current(self) -> VersionEntry | None:
        for v in reversed(self.versions):
            if v.is_active:
                return v
        return None

    @property
    def latest(self) -> VersionEntry | None:
        return self.versions[-1] if self.versions else None

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def is_unchanged(self) -> bool:
        return len(self.versions) <= 1


@dataclass(slots=True, frozen=True)
class AuditAction:
    action: str
    memory_id: UUID
    version: int
    is_active: bool
    supersedes: UUID | None
    superseded_by: UUID | None
    approved_by: UUID | None
    approved_at: str | None
    created_at: str | None


@runtime_checkable
class MemoryHistoryStore(Protocol):
    async def get_memory(self, memory_id: UUID) -> MemoryNode | None: ...
    async def get_all_versions(self, memory_id: UUID) -> list[MemoryNode]: ...


class MemoryHistory:
    def __init__(self, store: MemoryHistoryStore) -> None:
        self._store = store

    async def get_version_chain(self, memory_id: UUID) -> VersionChain:
        all_versions = await self._store.get_all_versions(memory_id)
        if not all_versions:
            return VersionChain(
                memory_id=memory_id,
                persona_id=UUID(int=0),
                tier=MemoryTier.ACCRUED,
                content_type="narrative",
            )

        root = all_versions[0]
        versions = sorted(
            [self._to_version_entry(n) for n in all_versions],
            key=lambda v: v.version,
        )
        return VersionChain(
            memory_id=memory_id,
            persona_id=root.persona_id,
            tier=root.tier if isinstance(root.tier, MemoryTier) else MemoryTier(root.tier),
            content_type=(
                root.content_type
                if isinstance(root.content_type, str)
                else root.content_type.value
            ),
            versions=versions,
        )

    async def get_full_audit_trail(self, memory_id: UUID) -> list[AuditAction]:
        chain = await self.get_version_chain(memory_id)
        trail: list[AuditAction] = []
        for entry in chain.versions:
            mem = entry.memory
            trail.append(
                AuditAction(
                    action="created" if entry.version == 1 and not mem.supersedes else "superseded",
                    memory_id=mem.id,
                    version=entry.version,
                    is_active=entry.is_active,
                    supersedes=mem.supersedes,
                    superseded_by=mem.superseded_by,
                    approved_by=mem.approved_by,
                    approved_at=(
                        mem.approved_at.isoformat() if mem.approved_at else None
                    ),
                    created_at=(
                        mem.created_at.isoformat() if mem.created_at else None
                    ),
                )
            )
        return trail

    async def diff_versions(
        self, older_id: UUID, newer_id: UUID
    ) -> dict[str, tuple[str | None, str | None]]:
        older = await self._store.get_memory(older_id)
        newer = await self._store.get_memory(newer_id)
        if older is None or newer is None:
            raise ValueError(f"Memory not found: older={older_id}, newer={newer_id}")

        fields = [
            "content",
            "content_type",
            "disclosure_scope",
            "source_type",
            "tier",
        ]
        diff: dict[str, tuple[str | None, str | None]] = {}
        for f in fields:
            old_val = getattr(older, f, None)
            new_val = getattr(newer, f, None)
            old_str = _val_to_str(old_val)
            new_str = _val_to_str(new_val)
            if old_str != new_str:
                diff[f] = (old_str, new_str)
        return diff

    async def is_quarantine_eligible(self, memory_id: UUID) -> bool:
        memory = await self._store.get_memory(memory_id)
        if memory is None:
            return False
        return memory.is_active and memory.tier != MemoryTier.CANONICAL

    @staticmethod
    def _to_version_entry(node: MemoryNode) -> VersionEntry:
        return VersionEntry(
            version=node.version,
            memory=node,
            is_active=node.is_active,
        )


def _val_to_str(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "value"):
        return val.value
    return str(val)
