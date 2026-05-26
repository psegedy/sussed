"""
sussed Agent Server - MCP-enabled AI real estate agent 🏠🤖

This runs an AgentOS server with MCP enabled, allowing VSCode Copilot
(or other MCP clients) to use our tools for analyzing listings.

Run with:
    uv run python -m sussed.agent.server

Or via CLI:
    uv run sussed agent
"""

import os
from loguru import logger

from agno.agent import Agent
from agno.tools import Toolkit

from sussed.agent.tools import SussedTools


# Agent instructions - the brain of the operation
AGENT_INSTRUCTIONS = '''You are an expert Czech real estate analyst helping users find great apartments.

## Your Workflow

1. **Pre-filter first**: Always start with `pre_filter_listings` to find candidates matching criteria
   - This uses existing database data - no API calls needed!
   - Filter by city, apartment type, price, area, features
   
2. **Check market stats**: Use `get_market_stats` to understand typical prices
   - Compare listing prices to averages
   - Price per m² is the key metric
   
3. **Fetch descriptions**: For promising candidates, use `fetch_listing_description`
   - Only fetch for listings that passed pre-filtering
   - Each fetch is an API call - use sparingly!
   
4. **Score listings**: After analysis, use `score_listing` to save your assessment
   - Extract parking prices (often separate from listing price!)
   - Calculate true usable area (exclude cellar, balcony from living space)
   - Note red flags and highlights

## Scoring Guide

- **0-200**: Trash tier - overpriced, bad location, or major issues
- **200-400**: Below average - some problems, not recommended
- **400-600**: Average - fair price, nothing special
- **600-800**: Good deal - worth considering seriously
- **800-1000**: Excellent - move fast before someone else does!
- **9999**: ABSOLUTE GEM - rare find, significantly underpriced, or perfect
- **-1**: SUS - likely scam, fake listing, or huge red flags

## What to Look For

### Red Flags 🚩
- Price too good to be true (way below market)
- Vague descriptions ("investment opportunity", "lots of potential")
- No floor plan or very few photos
- Unusual payment terms mentioned
- "Vibrant neighborhood" = loud AF
- "Cozy" = fucking tiny

### Good Signs ✅
- Detailed description with specifics
- Clear photos including floor plan
- Transparent pricing (parking included/excluded stated)
- New building or recent reconstruction
- Good energy rating (A, B, C)
- Garage or parking included

### Parking Analysis 🚗
- Parking is often priced separately (300k-800k CZK in Brno)
- Look for "parkovací stání", "garážové stání" in description
- Calculate TRUE total cost: listing price + parking
- Price per m² should use this total

### Area Calculation 📐
- Usable living area excludes: cellar (sklep), balcony, loggia, terrace
- These are often included in advertised m² to inflate size
- Calculate true price/m² using only actual living space
'''


def create_agent() -> Agent:
    """Create the sussed real estate agent."""
    return Agent(
        name="Sussed Real Estate Agent",
        tools=[SussedTools()],
        instructions=AGENT_INSTRUCTIONS,
        markdown=True,
        show_tool_calls=True,
    )


def run_server(host: str = "0.0.0.0", port: int = 7777):
    """
    Run the agent as an MCP server.
    
    Connect VSCode Copilot or other MCP clients to http://localhost:7777/mcp
    """
    try:
        from agno.playground import Playground, serve_playground_app
    except ImportError:
        logger.error("agno playground not available, trying alternative...")
        # Fall back to just creating the agent for direct use
        agent = create_agent()
        logger.info(f"Agent created: {agent.name}")
        logger.info("MCP server requires agno[playground] - run: uv add 'agno[playground]'")
        return agent
    
    agent = create_agent()
    
    # Create playground app (includes MCP endpoint)
    app = Playground(agents=[agent]).get_app()
    
    logger.info(f"🚀 Starting sussed agent server on http://{host}:{port}")
    logger.info(f"📡 MCP endpoint: http://{host}:{port}/mcp")
    logger.info("Connect VSCode Copilot to this endpoint!")
    
    serve_playground_app(app, host=host, port=port)


def run_cli_mode():
    """Run the agent in interactive CLI mode."""
    agent = create_agent()
    
    logger.info("🤖 sussed agent ready! Type your queries or 'quit' to exit.")
    logger.info("Example: 'Find me 2+kk apartments in Brno under 5M CZK'")
    print()
    
    while True:
        try:
            query = input("You: ").strip()
            if query.lower() in ("quit", "exit", "q"):
                print("Later! 👋")
                break
            if not query:
                continue
            
            # This would need an LLM configured to actually work
            # For now, just show what tools are available
            print("\nAgent tools available:")
            for tool in agent.tools:
                if hasattr(tool, '_functions'):
                    for name, func in tool._functions.items():
                        print(f"  - {name}: {func.__doc__.split(chr(10))[0] if func.__doc__ else 'No description'}")
            print("\n(Note: Full agent responses require LLM configuration)")
            print()
            
        except KeyboardInterrupt:
            print("\nLater! 👋")
            break
        except Exception as e:
            logger.error(f"Error: {e}")


if __name__ == "__main__":
    import sys
    
    if "--cli" in sys.argv:
        run_cli_mode()
    else:
        run_server()
