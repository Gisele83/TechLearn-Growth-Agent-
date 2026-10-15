from growth_agent.connectors.crm.base import (
    CRMAdapter,
    CRMCompany,
    CRMContact,
    CRMDeal,
    CRMNote,
    CRMTask,
)
from growth_agent.connectors.crm.factory import get_crm_adapter
from growth_agent.connectors.crm.hubspot import HubSpotCRMAdapter
from growth_agent.connectors.crm.mock import MockCRMAdapter

__all__ = [
    "CRMAdapter",
    "CRMCompany",
    "CRMContact",
    "CRMDeal",
    "CRMNote",
    "CRMTask",
    "HubSpotCRMAdapter",
    "MockCRMAdapter",
    "get_crm_adapter",
]
