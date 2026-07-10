"""工具1：网页读取工具。

输入 URL，抓取网页标题、正文、作者、发布时间、来源域名等元信息。
使用 httpx + beautifulsoup4 实现。
"""
from __future__ import annotations
from urllib.parse import urlparse
import re

from tools.base import Tool
from tools.credibility.models import PageMetadata, to_json


# ── HTML 清理：需要移除的标签 ──────────────────────────────
REMOVE_TAGS = {"script", "style", "nav", "footer", "header",
               "noscript", "iframe", "svg", "form", "input",
               "button", "select", "textarea", "canvas"}


def _extract_domain(url: str) -> str:
    """从 URL 中提取域名（不含 www. 前缀）。"""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    # 去掉常见的 www. 前缀
    hostname = re.sub(r"^www\d*\.", "", hostname, flags=re.IGNORECASE)
    return hostname


def _get_text_content(soup: "BeautifulSoup", max_chars: int) -> str:
    """从解析后的 HTML 中提取干净的文本内容。

    优先 <article>，否则用 <body>，去掉不可见标签的内容。
    """
    # 优先 article 标签
    article = soup.find("article")
    main_elem = article if article else soup.find("body")
    if not main_elem:
        main_elem = soup

    # 移除不需要的标签
    for tag in main_elem.find_all(REMOVE_TAGS):
        tag.decompose()

    # 移除隐藏元素（style="display:none" 等）
    for tag in main_elem.find_all(True):
        try:
            if tag.get("style") and re.search(r"display\s*:\s*none", tag["style"], re.IGNORECASE):
                tag.decompose()
                continue
            hidden = tag.get("aria-hidden")
            if hidden and hidden.lower() == "true":
                tag.decompose()
                continue
        except AttributeError:
            # BeautifulSoup 某些版本中 attrs 可能为 None，跳过即可
            continue

    text = main_elem.get_text(separator="\n", strip=True)
    # 合并多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 截断
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... 内容已截断 ...]"
    return text


def _extract_meta(soup: "BeautifulSoup", name: str) -> str:
    """从 <meta> 标签提取属性值，支持 name 和 property 两种写法。"""
    # <meta name="..." content="...">
    tag = soup.find("meta", attrs={"name": name, "content": True})
    if tag and tag.get("content"):
        return tag["content"].strip()
    # <meta property="og:..." content="...">
    tag = soup.find("meta", attrs={"property": name, "content": True})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def _webpage_reader(url: str, max_chars: int = 5000) -> str:
    """核心函数：抓取网页并提取结构化元信息。

    返回 JSON 字符串（PageMetadata 格式）。
    """
    result = PageMetadata(url=url)

    if not url or not url.startswith(("http://", "https://")):
        result.error = f"无效 URL：{url}，必须以 http:// 或 https:// 开头"
        return to_json(result)

    try:
        import httpx
    except ImportError:
        result.error = "缺少依赖 httpx，请执行 pip install httpx"
        return to_json(result)

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        result.error = "缺少依赖 beautifulsoup4，请执行 pip install beautifulsoup4"
        return to_json(result)

    # ── 1. 发起 HTTP 请求 ──────────────────────────────
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            })
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        result.error = f"HTTP 错误：{e.response.status_code} {e.response.reason_phrase}"
        return to_json(result)
    except httpx.TimeoutException:
        result.error = "请求超时（15秒），目标网站响应过慢"
        return to_json(result)
    except httpx.RequestError as e:
        result.error = f"请求失败：{e}"
        return to_json(result)

    # ── 2. 检查 Content-Type ──────────────────────────
    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        result.error = f"不支持的内容类型：{content_type}，仅支持 HTML 页面"
        result.extraction_success = False
        return to_json(result)

    # ── 3. 解析 HTML ──────────────────────────────────
    html_text = resp.text
    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception as e:
        result.error = f"HTML 解析失败：{e}"
        return to_json(result)

    # 标题
    try:
        title_tag = soup.find("title")
        result.title = title_tag.get_text(strip=True) if title_tag else ""

        # 描述
        result.description = _extract_meta(soup, "description") or _extract_meta(soup, "og:description")

        # 域名
        result.domain = _extract_domain(url)

        # 作者
        result.author = (
            _extract_meta(soup, "author")
            or _extract_meta(soup, "article:author")
            or ""
        )

        # 发布日期
        result.publication_date = (
            _extract_meta(soup, "date")
            or _extract_meta(soup, "article:published_time")
            or _extract_meta(soup, "publication_date")
            or ""
        )
        # 如果日期包含 T（ISO 8601），只取日期部分
        if result.publication_date and "T" in result.publication_date:
            result.publication_date = result.publication_date.split("T")[0]

        # 正文内容
        result.text_content = _get_text_content(soup, max_chars)

        # 字数统计
        words = result.text_content.split()
        result.word_count = len(words)

        result.extraction_success = True
    except Exception as e:
        import traceback
        result.error = f"页面解析过程出错：{e}\n{traceback.format_exc()[:500]}"
        result.extraction_success = False

    return to_json(result)


# ── 构造 Tool 实例 ────────────────────────────────────────
webpage_reader_tool = Tool(
    name="webpage_reader",
    description=(
        "抓取指定 URL 的网页内容，提取标题、正文、作者、发布日期、来源域名等结构化元信息。"
        "返回 JSON 格式的元数据，包含清理后的纯文本正文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "需要分析的目标网页 URL（必须以 http:// 或 https:// 开头）",
            },
            "max_chars": {
                "type": "integer",
                "description": "正文最大字符数（默认 5000，超长页面将截断",
                "default": 5000,
            },
        },
        "required": ["url"],
    },
    run=_webpage_reader,
)
