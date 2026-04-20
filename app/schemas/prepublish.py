from pydantic import BaseModel


class PrepublishPayload(BaseModel):
    invocationId: str
    datasetId: str
    datasetGlobalId: str
    datasetVersion: str
