"""net_deep_research — multi-source web truth-finding with source reputation.

Command line:

    net-deep-research "your question"
    net-deep-research --report "your question"

As a library:

    from net_deep_research import research
    result = research("your question", report=True)
    print(result["answer"], result["sources"], result["report_path"])
"""

from .cli import research

__version__ = "1.1.4"
__all__ = ["research", "__version__"]
