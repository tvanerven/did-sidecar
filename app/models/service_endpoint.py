from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DidServiceEndpoint(Base):
    __tablename__ = "did_service_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False, index=True)
    log_entry_id: Mapped[int] = mapped_column(ForeignKey("did_log_entries.id"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(String, nullable=False)
    endpoint_type: Mapped[str] = mapped_column(String, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    dataset = relationship("Dataset", back_populates="service_endpoints")
    log_entry = relationship("DidLogEntry", back_populates="service_endpoints")
