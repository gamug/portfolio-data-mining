"""
Extracts title and body content from markdown files produced by BulkCrawler.

Handles the header comments BulkCrawler writes (source_url, crawled_at) and
tries a few strategies to find the title, since not every site's markdown
puts it in the same place.
"""

import os
import re
from dataclasses import dataclass


@dataclass
class ExtractedArticle:
    title: str | None
    content: str
    source_url: str | None
    crawled_at: str | None


class MarkdownExtractor:
    """
    Parses a markdown file (as written by BulkCrawler) into title + content.

    Title detection strategy, in order:
        1. First H1 (`# Heading`) in the document
        2. First H2 if no H1 exists
        3. First non-empty line, if it's short (looks like a title, not a paragraph)
        4. None, if nothing plausible is found
    """

    _HEADER_COMMENT_RE = re.compile(r"^<!--\s*(\w+):\s*(.*?)\s*-->\s*$")
    _HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$")

    def __init__(self, max_title_line_length: int = 200):
        self.max_title_line_length = max_title_line_length

    def extract_from_text(self, markdown: str) -> ExtractedArticle:
        lines = markdown.splitlines()

        # --- Strip our own header comments (source_url / crawled_at) ---
        source_url = None
        crawled_at = None
        body_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "":
                body_start = i + 1
                continue
            if m := self._HEADER_COMMENT_RE.match(stripped):
                key, value = m.group(1), m.group(2)
                if key == "source_url":
                    source_url = value
                elif key == "crawled_at":
                    crawled_at = value
                body_start = i + 1
                continue
            break  # first non-comment, non-blank line — real content starts here

        body_lines = lines[body_start:]

        # --- Find the title ---
        title = None
        title_line_index = None

        for i, line in enumerate(body_lines):
            stripped = line.strip()
            if not stripped:
                continue
            if m := self._HEADING_RE.match(stripped):
                title = m.group(2).strip()
                title_line_index = i
                break
            # Not a heading — stop looking after the first non-empty line
            # unless it looks like a plausible bare title.
            if len(stripped) <= self.max_title_line_length and not stripped.startswith((">", "-", "*", "|")):
                title = stripped
                title_line_index = i
            break

        # --- Content is everything after the title line ---
        if title_line_index is not None:
            content_lines = body_lines[title_line_index + 1 :]
        else:
            content_lines = body_lines

        content = "\n".join(content_lines).strip()

        return ExtractedArticle(
            title=title,
            content=content,
            source_url=source_url,
            crawled_at=crawled_at,
        )

    def extract_from_file(self, filepath: str) -> ExtractedArticle:
        with open(filepath, "r", encoding="utf-8") as f:
            markdown = f.read()
        return self.extract_from_text(markdown)

    def extract_from_directory(self, directory: str, pattern: str = "*.md") -> list[ExtractedArticle]:
        import glob

        results = []
        for filepath in sorted(glob.glob(os.path.join(directory, pattern))):
            if os.path.basename(filepath).startswith("_"):  # skip _crawl_state.json etc.
                continue
            results.append(self.extract_from_file(filepath))
        return results


# ---------------------------------------------------------------------- #
# Example usage
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    extractor = MarkdownExtractor()

    # Single file
    article = extractor.extract_from_file(
        "/path/to/general_markdown_dir/2026-08-05/cnbc.com__some-article__a1b2c3d4.md"
    )
    print("TITLE:", article.title)
    print("SOURCE:", article.source_url)
    print("CONTENT PREVIEW:", article.content[:300])

    # Whole directory (e.g. one day's crawl)
    articles = extractor.extract_from_directory("/path/to/general_markdown_dir/2026-08-05")
    print(f"\nExtracted {len(articles)} articles.")
    for a in articles[:5]:
        print(f"- {a.title}  ({a.source_url})")