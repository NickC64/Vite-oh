from markdown_it import MarkdownIt
from markupsafe import Markup

_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "html": False,
        "linkify": False,
        "typographer": False,
    },
)


def safe_markdown(value: str) -> Markup:
    """Render the small, HTML-free Markdown subset supported by Discord content."""
    return Markup(_MARKDOWN.render(value))
