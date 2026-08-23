"""md_to_pdf — 研究报告 Markdown → PDF 渲染器（纯标准库，无第三方依赖）。

机制：内置极简 Markdown → HTML 转换器（覆盖报告固定子集：标题/表格/列表/
分隔线/段落/加粗/行内代码）+ 排版 CSS，自动探测系统 Chrome/Chromium/Edge
做 headless 打印。能渲染就出 PDF，找不到浏览器或渲染失败返回 False，由调用
方决定降级为 Markdown。

命令行用法：
    python3 md_to_pdf.py report.md [report.pdf]
    成功：stdout 输出 PDF 路径，退出码 0
    失败：stderr 输出原因，退出码 1（调用方应保留 Markdown 版本）
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile

_CSS = """
body { font-family: -apple-system, 'PingFang SC', 'Hiragino Sans GB',
       'Microsoft YaHei', 'Helvetica Neue', sans-serif;
       max-width: 860px; margin: 36px auto; padding: 0 20px;
       color: #1f2328; line-height: 1.65; font-size: 13px; }
h1 { font-size: 22px; border-bottom: 2px solid #d0d7de; padding-bottom: 8px; }
h2 { font-size: 17px; margin-top: 26px; border-bottom: 1px solid #e4e8ec;
     padding-bottom: 4px; }
h3 { font-size: 14.5px; margin-top: 18px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0;
        font-size: 12px; }
th, td { border: 1px solid #d0d7de; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #f6f8fa; }
ul { padding-left: 22px; }
li { margin: 3px 0; }
hr { border: none; border-top: 1px solid #e4e8ec; margin: 18px 0; }
code { background: #f6f8fa; padding: 1px 4px; border-radius: 3px;
       font-size: 12px; }
p { margin: 8px 0; }
"""

_BROWSER_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "chromium", "chromium-browser", "msedge",
]


def _md_inline(text: str) -> str:
    """行内 Markdown（加粗/代码）转 HTML。"""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def md_to_html(md: str) -> str:
    """报告固定子集（标题/表格/列表/分隔线/段落）转 HTML。"""
    parts: list[str] = []
    in_table = False
    in_ul = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if "|" in line and set(line.replace("|", "").strip()) <= set("-: ") \
                and re.search(r"-{3,}", line):
            continue  # 表格分隔行
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                parts.append("<table>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            row = "".join(f"<{tag}>{_md_inline(c)}</{tag}>" for c in cells)
            parts.append(f"<tr>{row}</tr>")
            continue
        if in_table:
            parts.append("</table>")
            in_table = False
        if re.match(r"^\s*[-*] ", line):
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append(f"<li>{_md_inline(line.lstrip()[2:])}</li>")
            continue
        if in_ul:
            parts.append("</ul>")
            in_ul = False
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            parts.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
        elif re.match(r"^\s*-{3,}\s*$", line):
            parts.append("<hr>")
        elif line.strip():
            parts.append(f"<p>{_md_inline(line)}</p>")
    if in_table:
        parts.append("</table>")
    if in_ul:
        parts.append("</ul>")
    return "\n".join(parts)


def find_browser() -> str | None:
    """探测系统 Chrome/Chromium/Edge 可执行文件。"""
    from shutil import which
    for path in _BROWSER_CANDIDATES:
        if os.path.isabs(path):
            if os.path.exists(path):
                return path
        else:
            found = which(path)
            if found:
                return found
    return None


def render_pdf(md_text: str, pdf_path: str) -> bool:
    """Markdown 文本 → PDF 文件。成功返回 True；无浏览器或渲染失败返回 False。"""
    browser = find_browser()
    if not browser:
        return False
    html_doc = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<style>{_CSS}</style></head><body>"
                f"{md_to_html(md_text)}</body></html>")
    fd, html_path = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html_doc)
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path],
            capture_output=True, timeout=90)
        return proc.returncode == 0 and os.path.exists(pdf_path)
    except Exception:
        return False
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 md_to_pdf.py <input.md> [output.pdf]",
              file=sys.stderr)
        return 2
    md_path = sys.argv[1]
    pdf_path = (sys.argv[2] if len(sys.argv) > 2
                else os.path.splitext(md_path)[0] + ".pdf")
    try:
        with open(md_path, encoding="utf-8") as fh:
            md_text = fh.read()
    except OSError as exc:
        print(f"读取失败: {exc}", file=sys.stderr)
        return 2
    if not find_browser():
        print("未检测到 Chrome/Chromium/Edge，无法渲染 PDF", file=sys.stderr)
        return 1
    if render_pdf(md_text, pdf_path):
        print(pdf_path)
        return 0
    print("PDF 渲染失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
