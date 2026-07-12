"""图像工具：编码图片为多模态内容块 + 读取图片工具。

让 agent 的输入从纯文本扩展到「文本 + 图像」。
"""
from __future__ import annotations
import base64
from io import BytesIO
from pathlib import Path

from tools.base import Tool


def image_block(path: str, media_type: str | None = None) -> dict:
    """把一张图片编码为多模态内容块（Anthropic/OpenAI 兼容格式）。

    大图自动缩放（长边 ≤ 1568px），省 token、避免被拒。
    """
    path = Path(path)
    if not path.exists():
        return {"type": "text", "text": f"[图片不存在：{path}]"}

    # 自动检测媒体类型
    if media_type is None:
        suffix = path.suffix.lower()
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/png")

    data = path.read_bytes()

    # 大图缩放（长边 > 1568px 则等比缩小）
    try:
        from PIL import Image
        img = Image.open(BytesIO(data))
        w, h = img.size
        max_side = 1568
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format=img.format or "PNG")
            data = buf.getvalue()
    except ImportError:
        pass  # 没装 Pillow 就原图发送

    b64 = base64.b64encode(data).decode()

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": b64,
        },
    }


def _read_image(path: str, question: str = "请描述这张图片的内容") -> str:
    """读取图片并返回多模态消息描述。

    返回的是模型对图片的描述文本。
    """
    block = image_block(path)
    if block["type"] == "text":
        return block["text"]

    # 这是工具返回，给模型看的——直接返回图片已编码的信息
    return (f"[图片已读取] {path}\n"
            f"大小: {Path(path).stat().st_size / 1024:.1f} KB\n"
            f"图片已编码为 base64 内容块，可在下轮对话中由支持视觉的模型查看。")


read_image_tool = Tool(
    name="read_image",
    description="读取一张图片并编码为多模态内容块，供支持视觉的模型查看。输入图片路径和可选问题。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "图片文件路径"},
            "question": {"type": "string", "description": "针对图片的问题，默认'请描述这张图片的内容'"},
        },
        "required": ["path"],
    },
    run=_read_image,
)
