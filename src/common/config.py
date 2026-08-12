import os
from pathlib import Path

# Repo root = two levels up from this file (src/config/__init__.py -> src/ -> repo root),
# unless overridden explicitly via PORTFOLIO_KG_DATA_DIR (e.g. when deployed elsewhere).
# Using an absolute, file-anchored base fixes the old behavior where every path was
# relative to '..' and therefore silently depended on the process's current working
# directory (broke as soon as the app was launched from a different cwd).
_DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(os.environ.get("PORTFOLIO_KG_DATA_DIR", _DEFAULT_BASE_DIR)).resolve()

OUTPUT_DIR = BASE_DIR / "output"
INPUT_DIR = BASE_DIR / "input"

general = {
    'paths': {
        'output': str(OUTPUT_DIR),
        'news': str(OUTPUT_DIR / "news"),
        'news_links': str(OUTPUT_DIR / "news" / "links"),
        'news_markdown': str(OUTPUT_DIR / "news" / "markdown"),
        'news_json': str(OUTPUT_DIR / "news" / "json"),
        'logs': str(OUTPUT_DIR / "logs"),
        'sec_filings': str(OUTPUT_DIR / "sec_filings"),
        'input': str(INPUT_DIR),
        'trading': str(OUTPUT_DIR / "trading"),
    },
    'domains': [
                "cnbc.com", "finance.yahoo.com", "ft.com",
                "investing.com", "Nasdaq.com", "seekingalpha.com",
                "stocktwits.com"
            ]
}
