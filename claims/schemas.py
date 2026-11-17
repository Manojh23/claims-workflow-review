from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

DocKind = Literal["denial_notice", "submission_receipt", "authorization_record", "cover_sheet"]
Reason = Literal["missing_receipt", "missing_auth_record", "identifier_mismatch", "unknown"]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1800)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(Model):
    kind: DocKind
    text: Nonempty


class NewCase(Model):
    claim_ref: Annotated[str, StringConstraints(pattern=r"^DEMO-[A-Z0-9-]{1,32}$")]
    documents: Annotated[list[Document], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def distinct_documents(self):
        kinds = [d.kind for d in self.documents]
        if len(set(kinds)) != len(kinds) or "denial_notice" not in kinds:
            raise ValueError("Provide one denial notice and at most one document of each type.")
        return self


class Draft(Model):
    reason: Reason
    quote: Annotated[str, Field(min_length=8, max_length=240)]


class Decision(Model):
    outcome: Literal["approve", "return"]
    version: Annotated[int, Field(ge=1)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
