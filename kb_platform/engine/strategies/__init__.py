"""Strategy registry bootstrap."""

from kb_platform.engine.strategy import register_strategy
from kb_platform.engine.strategies.community_reports import CommunityReportsStrategy
from kb_platform.engine.strategies.extract_graph import ExtractGraphStrategy
from kb_platform.engine.strategies.summarize_descriptions import SummarizeDescriptionsStrategy

register_strategy("extract_graph", ExtractGraphStrategy())
register_strategy("summarize_descriptions", SummarizeDescriptionsStrategy())
register_strategy("community_reports", CommunityReportsStrategy())
