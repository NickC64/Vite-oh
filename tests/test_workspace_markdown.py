from viteoh.workspace_markdown import safe_markdown


def test_workspace_markdown_preserves_formatting_without_html_or_unsafe_links() -> None:
    rendered = str(
        safe_markdown(
            "**Useful** [site](https://example.com) "
            "<script>alert(1)</script> [bad](javascript:alert(1))"
        )
    )
    assert "<strong>Useful</strong>" in rendered
    assert 'href="https://example.com"' in rendered
    assert "<script>" not in rendered
    assert 'href="javascript:' not in rendered
