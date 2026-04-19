from pydantic import BaseModel


class PrepublishPayload(BaseModel):
    invocationId: str
    datasetId: str
    datasetPid: str
    datasetVersion: str
