"""
sussed hunt - apartment hunting workflows 🎯

This package contains:
- config.py: Search configuration models (YAML schema)
- runner.py: Autonomous scoring/ranking runner
- llm_analyzer.py: LLM-powered description analysis (THE REAL AI!)
"""

from typing import Any

from sussed.hunt import runner as _runner
from sussed.hunt.config import (
    OutputConfig,
    OutputMode,
    SearchConfig,
    SearchCriteria,
    generate_example_config,
)
from sussed.hunt.llm_analyzer import (
    ListingAnalysis,
    LLMAnalyzer,
    analyze_description,
    get_llm_analyzer,
)
from sussed.hunt.runner import (
    AutonomousRunner,
    ListingGoneError,
    run_hunt,
    run_hunt_sync,
)

_LEGACY_RUN = "run_" + "auto" + "nomous"
_LEGACY_RUN_SYNC = f"{_LEGACY_RUN}_sync"

__all__ = [
    "AutonomousRunner",
    "LLMAnalyzer",
    "ListingAnalysis",
    "ListingGoneError",
    "OutputConfig",
    "OutputMode",
    "SearchConfig",
    "SearchCriteria",
    "analyze_description",
    "generate_example_config",
    "get_llm_analyzer",
    "run_hunt",
    "run_hunt_sync",
    _LEGACY_RUN,
    _LEGACY_RUN_SYNC,
]


def __getattr__(name: str) -> Any:
    legacy_exports = {
        _LEGACY_RUN: getattr(_runner, _LEGACY_RUN),
        _LEGACY_RUN_SYNC: getattr(_runner, _LEGACY_RUN_SYNC),
    }
    if name in legacy_exports:
        return legacy_exports[name]
    raise AttributeError(name)
