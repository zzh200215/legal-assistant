from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConnectorOut(BaseModel):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    connector_type: str
    name: str
    status: str
    config_json: str | None = None
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_imported_count: int = 0
    last_skipped_count: int = 0
    total_imported_count: int = 0
    total_skipped_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

