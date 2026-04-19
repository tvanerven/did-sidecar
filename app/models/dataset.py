import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataverse_pid: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    did: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    pid_url: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    did_log_entries = relationship("DidLogEntry", back_populates="dataset", cascade="all, delete-orphan")
    service_endpoints = relationship("DidServiceEndpoint", back_populates="dataset", cascade="all, delete-orphan")
