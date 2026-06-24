"""Strategy registry bootstrap."""

from kb_platform.engine.strategy import register_strategy
from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy

register_strategy("extract_graph", ExtractGraphStrategy())
