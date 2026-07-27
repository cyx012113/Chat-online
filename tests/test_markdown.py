from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from chat_online.theme import apply_theme
from chat_online.widgets import BubbleRow, _render_markdown


APP = QApplication.instance() or QApplication([])


def test_common_markdown_and_gfm_features_render() -> None:
    rendered = _render_markdown(
        "# 标题\n\n**粗体** *斜体* ~~删除~~ `code`  \n下一行\n\n"
        "> 外层\n> > 内层\n\n- 项目\n1. 编号\n\n---"
    )

    assert "<h1>标题</h1>" in rendered
    assert "<strong>粗体</strong>" in rendered
    assert "<em>斜体</em>" in rendered
    assert "<s>删除</s>" in rendered
    assert "<code>code</code>" in rendered
    assert "<br />" in rendered
    assert rendered.count("<blockquote>") == 2
    assert "<ul>" in rendered
    assert "<ol>" in rendered
    assert "<hr />" in rendered


def test_table_alignment_task_lists_and_cute_table_directive() -> None:
    rendered = _render_markdown(
        "::cute-table{tuack}\n\n"
        "| 左 | 右 |\n|:---|---:|\n| A | B |\n\n"
        "- [ ] 未完成\n- [x] 已完成"
    )

    assert "cute-table" not in rendered
    assert "<table>" in rendered
    assert 'style="text-align:left"' in rendered
    assert 'style="text-align:right"' in rendered
    assert "&#x2610;" in rendered
    assert "&#x2611;" in rendered
    assert "<input" not in rendered


def test_fenced_code_supports_language_numbers_and_highlight_range() -> None:
    rendered = _render_markdown(
        "```python line-numbers lines=2-3\n"
        "def hello():\n"
        "    print('hello')\n"
        "    return True\n"
        "```"
    )

    assert 'class="md-code-block"' in rendered
    assert 'data-language="python"' in rendered
    assert 'data-line="1"' in rendered
    assert 'data-line="3"' in rendered
    assert rendered.count("background-color:#FFF2A8") == 2
    assert "line-numbers" not in rendered

    fallback = _render_markdown("```\n#include <iostream>\n```")
    assert 'data-language="cpp"' in fallback


def test_links_and_images_use_a_strict_scheme_allowlist() -> None:
    rendered = _render_markdown(
        "[安全](https://example.com) "
        "[文件](file:///tmp/secret) "
        "[脚本](javascript:alert(1))\n\n"
        "![示例](https://example.com/image.png)\n\n"
        "![危险](data:text/html;base64,AAAA)\n\n"
        "![](bilibili:BV1GJ411x7h7?page=4&t=82)"
    )

    assert 'href="https://example.com"' in rendered
    assert 'href="file:' not in rendered
    assert 'href="javascript:' not in rendered
    assert 'href="data:' not in rendered
    assert "<img" not in rendered
    assert "[图片：示例]" in rendered
    assert "链接已拦截" in rendered
    assert 'href="https://www.bilibili.com/video/BV1GJ411x7h7?p=4&amp;t=82"' in rendered


def test_raw_html_is_escaped_and_latex_source_is_preserved() -> None:
    rendered = _render_markdown(
        "<script>alert('x')</script>\n\n"
        "行内 $a < b$\n\n$$\nx^2 + y^2\n$$"
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "class=\"md-math\"" in rendered
    assert "&#36;a &lt; b&#36;" in rendered
    assert "&#36;&#36;" in rendered


def test_luogu_static_containers_are_safe_and_nested() -> None:
    rendered = _render_markdown(
        ":::align{center}\n**居中**\n::: \n\n"
        ":::epigraph[作者]\n引言\n:::\n\n"
        "::::warning[注意]{open}\n"
        ":::info[内部]\n内容\n:::\n"
        "::::"
    )

    assert 'align="center"' in rendered
    assert "md-epigraph" in rendered
    assert "-- 作者" in rendered
    assert "md-warning" in rendered
    assert "md-info" in rendered
    assert 'data-open="true"' in rendered


def test_bubble_keeps_raw_packet_for_search_and_file_action() -> None:
    apply_theme(APP, "light")
    markdown = "**needle**"
    row = BubbleRow({"type": "message", "text": markdown})
    assert row.packet()["text"] == markdown
    assert "<strong>needle</strong>" in row._body.text()

    file_row = BubbleRow(
        {
            "type": "file_shared",
            "file": {"id": "file-1", "name": "report.md", "size": 12},
        }
    )
    assert "共享文件：report.md" in file_row._body.text()
    assert not file_row._file_button.isHidden()
    row.close()
    file_row.close()


def test_markdown_bubble_respects_compact_width_without_collapsing() -> None:
    apply_theme(APP, "light")
    row = BubbleRow(
        {
            "type": "message",
            "sender": "Bob",
            "text": (
                "### Build ready\n\n- [x] TLS 1.3\n\n"
                "```python line-numbers lines=2-2\n"
                "status = 'secure'\nprint(status)\n```"
            ),
        }
    )
    row.setFixedWidth(340)
    row.set_maximum_bubble_width(280)
    row.show()
    APP.processEvents()

    assert 140 <= row._bubble.width() <= 280
    assert 100 <= row._body.width() <= 254
    assert row._body.height() >= row._body.sizeHint().height()
    row.close()
