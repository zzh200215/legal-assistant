from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, DateTime, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)
    description = Column(String(512), nullable=True)
    variables = Column(String(512), nullable=True)
    active_version_id = Column(Integer, ForeignKey("prompt_template_versions.id"), nullable=True)
    previous_active_version_id = Column(Integer, ForeignKey("prompt_template_versions.id"), nullable=True)
    rollout_version_id = Column(Integer, ForeignKey("prompt_template_versions.id"), nullable=True)
    rollout_percentage = Column(Integer, nullable=False, default=0)
    rollout_started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    active_version = relationship("PromptTemplateVersion", foreign_keys=[active_version_id], post_update=True)
    previous_active_version = relationship("PromptTemplateVersion", foreign_keys=[previous_active_version_id], post_update=True)
    rollout_version = relationship("PromptTemplateVersion", foreign_keys=[rollout_version_id], post_update=True)
    versions = relationship(
        "PromptTemplateVersion",
        back_populates="template_ref",
        foreign_keys="PromptTemplateVersion.template_id",
        cascade="all, delete-orphan",
    )


class PromptTemplateVersion(Base):
    __tablename__ = "prompt_template_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    template = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    change_note = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    template_ref = relationship("PromptTemplate", back_populates="versions", foreign_keys=[template_id])
