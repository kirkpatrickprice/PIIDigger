from pydantic import BaseModel, ConfigDict


class PiiDiggerModel(BaseModel):
    """Base model for all PIIDigger Pydantic models.

    Sets extra="forbid" so that unknown field names raise at construction time
    rather than silently being dropped. This is especially valuable for models
    that cross the process boundary via Task.payload deserialization.

    Subclasses may add their own model_config entries (e.g. frozen=True);
    Pydantic v2 merges child config with the parent, child values winning.
    """

    model_config = ConfigDict(extra="forbid")
