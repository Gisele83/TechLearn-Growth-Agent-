"""Agents spécialisés.

Chaque agent a une responsabilité unique et un garde-fou explicite, consigné
dans sa docstring et appliqué dans son code. Aucun agent n'envoie quoi que ce
soit vers l'extérieur : ils produisent des propositions que le workflow
d'approbation transforme — ou non — en actions.
"""

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.agents.conversation_analyst import ConversationAnalyst
from growth_agent.agents.customer_success import CustomerSuccessAgent
from growth_agent.agents.growth_analyst import GrowthAnalystAgent
from growth_agent.agents.lead_intelligence import LeadIntelligenceAgent
from growth_agent.agents.market_scout import MarketScoutAgent
from growth_agent.agents.outreach_composer import OutreachComposerAgent
from growth_agent.agents.proposal_assistant import ProposalAssistantAgent
from growth_agent.agents.sales_coordinator import SalesCoordinatorAgent

__all__ = [
    "AgentContext",
    "AgentResult",
    "BaseAgent",
    "ConversationAnalyst",
    "CustomerSuccessAgent",
    "GrowthAnalystAgent",
    "LeadIntelligenceAgent",
    "MarketScoutAgent",
    "OutreachComposerAgent",
    "ProposalAssistantAgent",
    "SalesCoordinatorAgent",
]
