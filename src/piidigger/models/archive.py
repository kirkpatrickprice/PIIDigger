from __future__ import annotations

from pydantic import ConfigDict, Field

from piidigger.models.base import PiiDiggerModel


class MemberInfo(PiiDiggerModel):
    """Format-neutral descriptor for one entry in an archive.

    Produced by ArchiveHandler.list_members(); consumed by
    handle_enum_archive_members() to apply safety checks.
    Directory entries are included (is_dir=True) so the member-count
    stop logic counts non-directory members correctly.

    decompress_offset is how many uncompressed bytes a reader must get through
    before reaching this member's data: its offset in a compressed tar stream,
    or in its 7z folder.  It is 0 for zip and for an uncompressed tar, which
    reach any member without decompressing anything.  Enumeration
    caps it, so no scan task has to decompress an unbounded amount of data to
    reach a member.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int = Field(ge=0)
    is_dir: bool
    is_encrypted: bool
    decompress_offset: int = Field(default=0, ge=0)
