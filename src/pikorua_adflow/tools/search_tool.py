"""
SerperDev search tool wrapper for use by audience crew agents.
Requires SERPER_API_KEY in .env.
"""
from crewai_tools import SerperDevTool

# Shared instance — import this in crew files rather than instantiating per agent
search_tool = SerperDevTool()
