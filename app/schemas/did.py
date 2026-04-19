from pydantic import BaseModel


class DidService(BaseModel):
    id: str
    type: str
    serviceEndpoint: str


class DidState(BaseModel):
    id: str
    service: list[DidService]


class DidLogEntryPayload(BaseModel):
    versionId: str
    versionTime: str
    parameters: dict
    state: dict
    proof: dict
