"""
sussed agent - The AI brain that susses out the market 🧠

This package contains:
- tools.py: Custom toolkit with DB queries and API calls
- server.py: AgentOS MCP server setup
- config.py: Search configuration models (YAML schema)
- autonomous.py: Autonomous scoring/ranking runner
- llm_analyzer.py: LLM-powered description analysis (THE REAL AI!)
"""

from sussed.agent.tools import SussedTools
from sussed.agent.server import create_agent, run_server
from sussed.agent.config import SearchConfig, SearchCriteria, OutputConfig, OutputMode, generate_example_config
from sussed.agent.autonomous import AutonomousRunner, run_autonomous, run_autonomous_sync
from sussed.agent.llm_analyzer import (
    LLMAnalyzer,
    ListingAnalysis,
    get_llm_analyzer,
    analyze_description,
)

__all__ = [
    "SussedTools",
    "create_agent",
    "run_server",
    "SearchConfig",
    "SearchCriteria",
    "OutputConfig",
    "OutputMode",
    "generate_example_config",
    "AutonomousRunner",
    "run_autonomous",
    "run_autonomous_sync",
    "LLMAnalyzer",
    "ListingAnalysis",
    "get_llm_analyzer",
    "analyze_description",
]
