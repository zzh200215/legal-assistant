from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict


class OrganizationCreate(BaseModel):
    name: str
    code: str
    description: Optional[str] = None


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class DepartmentCreate(BaseModel):
    organization_id: int
    name: str
    code: str
    description: Optional[str] = None


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class UserOrgAssignRequest(BaseModel):
    organization_id: Optional[int] = None
    department_id: Optional[int] = None
    job_title: Optional[str] = None


class OrganizationOut(BaseModel):
    id: int
    name: str
    code: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DepartmentOut(BaseModel):
    id: int
    organization_id: int
    name: str
    code: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DepartmentWithOrgOut(DepartmentOut):
    organization: Optional[OrganizationOut] = None


class OrgSyncRequest(BaseModel):
    provider: str  # wecom / dingtalk / ldap


class OrgSyncResultOut(BaseModel):
    organizations_created: int = 0
    organizations_updated: int = 0
    departments_created: int = 0
    departments_updated: int = 0
    users_created: int = 0
    users_updated: int = 0
    users_disabled: int = 0
    errors: List[str] = []