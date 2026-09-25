from __future__ import annotations

from pydantic import ConfigDict, Field

from piidigger.models.base import PiiDiggerModel


class MemberInfo(PiiDiggerModel):
    """Format-neutral descriptor for one entry in an archive.

    Produced by ArchiveHandler.list_members(); consumed by
    handle_enum_archive_members() to apply safety checks.
    Directory entries are included (is_dir=True) so the member-count
    stop logic counts non-directory members correctly.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int = Field(ge=0)
    is_dir: bool
    is_encrypted: bool
