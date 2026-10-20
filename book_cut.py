# coding=utf-8
"""把拍摄或扫描的书页 PDF 切开，并补空白页对齐双面打印。

处理规则：
- 不看纸张尺寸，只看整份 PDF 的拍法。
  大部分页面横向更长（横拍）：横向更长的页面是双页，左右竖切；
  竖向更长的页面是单页，不切。
  大部分页面竖向更长（竖拍）：竖向更长的页面是双页，上下横切，上半边为左页；
  横向更长的页面是单页，不切。
- 切线有两种模式：
  fixed：左（上）页取 0 到 left_end，右（下）页取 right_start 到 100%。
  auto：书越往后翻，左侧拱起越多，切线随原 PDF 页号逐步向右（下）移动。
- 切开后的左页必须落在输出 PDF 的偶数页，右页在紧接着的奇数页。
  位置不对时，先补一张和切开后左页一样大的空白页。
- 页面原来的方向保持不变，不做旋转。

用法：
    python3 book_cut.py 文件.pdf
    python3 book_cut.py 文件.pdf --cut fixed --left-end 0.53 --right-start 0.47
    python3 book_cut.py 文件.pdf --cut auto --auto-step 0.0004
    python3 book_cut.py 文件.pdf --page 2,7=single --page 5=split
    python3 book_cut.py 文件.pdf --shot portrait
    python3 book_cut.py              # 处理当前目录下所有 PDF
"""
import argparse
import sys
from copy import copy
from dataclasses import dataclass
from os import listdir
from os.path import splitext

from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import RectangleObject

MM_PER_POINT = 25.4 / 72

LANDSCAPE = "landscape"
PORTRAIT = "portrait"
SHOT_NAMES = {LANDSCAPE: "横拍", PORTRAIT: "竖拍"}

SINGLE = "single"
SPLIT = "split"


@dataclass
class CutSettings:
    mode: str = "fixed"
    # 固定模式：左（上）页取 0 到 left_end，右（下）页取 right_start 到 1。
    left_end: float = 0.52
    right_start: float = 0.48
    # 自动模式：原 PDF 第 1 页切线在 auto_start，之后每页移动 auto_step，最多到 auto_max。
    # 默认第 1 页 50%，第 50 页 52%。
    auto_start: float = 0.50
    auto_step: float = 0.02 / 49
    auto_max: float = 0.60

    def split_fractions(self, source_no):
        """返回 (左页结束位置, 右页起始位置)，以宽度或高度的比例表示。"""
        if self.mode == "auto":
            line = min(self.auto_max, self.auto_start + self.auto_step * (source_no - 1))
            return line, line
        return self.left_end, self.right_start

    def describe(self):
        if self.mode == "auto":
            return (
                f"自动：第1页切线 {self.auto_start:.1%}，每页移动 {self.auto_step:.4%}，"
                f"最多 {self.auto_max:.1%}"
            )
        return f"固定：左（上）页 0–{self.left_end:.0%}，右（下）页 {self.right_start:.0%}–100%"


def output_path(input_path):
    base, _ext = splitext(input_path)
    return base + "-print.pdf"


def report_path(input_path):
    base, _ext = splitext(input_path)
    return base + "-print-report.txt"


def _base_box(page):
    """页面实际显示范围：有 CropBox 用 CropBox，没有就用 MediaBox。"""
    box = page.cropbox if "/CropBox" in page else page.mediabox
    return float(box.left), float(box.bottom), float(box.right), float(box.top)


def _rotation(page):
    return int(page.get("/Rotate") or 0) % 360


def display_size(page):
    """把 /Rotate 算进去后，阅读器里看到的宽和高（point）。"""
    x0, y0, x1, y1 = _base_box(page)
    width, height = x1 - x0, y1 - y0
    if _rotation(page) in (90, 270):
        return height, width
    return width, height


def page_shape(width, height):
    if width > height:
        return LANDSCAPE
    if height > width:
        return PORTRAIT
    return None


def detect_shot(sizes):
    """返回整份 PDF 的拍法，以及横向、竖向页面的数量。"""
    landscape = sum(1 for width, height in sizes if page_shape(width, height) == LANDSCAPE)
    portrait = sum(1 for width, height in sizes if page_shape(width, height) == PORTRAIT)
    # 一样多时按横拍处理，报告里会写出数量，方便核对。
    shot = LANDSCAPE if landscape >= portrait else PORTRAIT
    return shot, landscape, portrait


def classify(sizes, shot):
    """和整份 PDF 拍法一致的页面是双页，其余是单页。"""
    return [SPLIT if page_shape(width, height) == shot else SINGLE for width, height in sizes]


def parse_overrides(values, page_count):
    """解析 --page 参数，例如 "3,5-7=split"、"2=single"。"""
    overrides = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit(f"--page 参数格式不对：{value}，应为 页号=split|single")
        pages_part, action = value.split("=", 1)
        action = action.strip().lower()
        if action not in (SINGLE, SPLIT):
            raise SystemExit(f"--page 参数里的处理方式只能是 split、single：{value}")
        for chunk in pages_part.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start, end = (int(part) for part in chunk.split("-", 1))
            else:
                start = end = int(chunk)
            for number in range(start, end + 1):
                if not 1 <= number <= page_count:
                    raise SystemExit(f"--page 页号超出范围（共 {page_count} 页）：{number}")
                overrides[number] = action
    return overrides


def _display_rect_to_box(page, fx0, fx1, fy0, fy1):
    """把显示画面里的比例区域换算回页面坐标。

    fx 从显示画面左边算起，fy 从显示画面上边算起，都是 0 到 1。
    """
    x0, y0, x1, y1 = _base_box(page)
    width, height = x1 - x0, y1 - y0
    rotation = _rotation(page)
    if rotation == 0:
        return (x0 + fx0 * width, y1 - fy1 * height, x0 + fx1 * width, y1 - fy0 * height)
    if rotation == 90:
        return (x0 + fy0 * width, y0 + fx0 * height, x0 + fy1 * width, y0 + fx1 * height)
    if rotation == 180:
        return (x1 - fx1 * width, y0 + fy0 * height, x1 - fx0 * width, y0 + fy1 * height)
    return (x1 - fy1 * width, y1 - fx1 * height, x1 - fy0 * width, y1 - fx0 * height)


def _crop(page, fx0, fx1, fy0, fy1):
    box = RectangleObject(_display_rect_to_box(page, fx0, fx1, fy0, fy1))
    page.mediabox = box
    page.cropbox = box
    return page


def split_page(page, shot, left_end, right_start):
    """返回 (左页, 右页)。横拍时左右竖切；竖拍时上下横切，上半边为左页。"""
    right_page = copy(page)
    if shot == LANDSCAPE:
        left = _crop(page, 0.0, left_end, 0.0, 1.0)
        right = _crop(right_page, right_start, 1.0, 0.0, 1.0)
    else:
        left = _crop(page, 0.0, 1.0, 0.0, left_end)
        right = _crop(right_page, 0.0, 1.0, right_start, 1.0)
    return left, right


def _left_half_size(width, height, shot, left_end):
    if shot == LANDSCAPE:
        return width * left_end, height
    return width, height * left_end


def process(input_path, override_values=None, cut=None, forced_shot=None):
    cut = cut or CutSettings()
    reader = PdfReader(input_path)
    sizes = [display_size(page) for page in reader.pages]
    shot, landscape_count, portrait_count = detect_shot(sizes)
    if forced_shot:
        shot = forced_shot
    actions = classify(sizes, shot)
    overrides = parse_overrides(override_values, len(sizes))
    for number, action in overrides.items():
        actions[number - 1] = action

    writer = PdfWriter()
    singles = []
    blank_count = 0
    for number, (page, action, (width, height)) in enumerate(zip(reader.pages, actions, sizes), start=1):
        if action == SINGLE:
            writer.add_page(page)
            singles.append((number, width, height, len(writer.pages), number in overrides))
            continue

        left_end, right_start = cut.split_fractions(number)
        if (len(writer.pages) + 1) % 2 == 1:
            blank_width, blank_height = _left_half_size(width, height, shot, left_end)
            writer.add_blank_page(width=blank_width, height=blank_height)
            blank_count += 1
        left, right = split_page(page, shot, left_end, right_start)
        writer.add_page(left)
        writer.add_page(right)

    out = output_path(input_path)
    with open(out, "wb") as output_file:
        writer.write(output_file)

    report = build_report(
        input_path,
        out,
        shot,
        landscape_count,
        portrait_count,
        bool(forced_shot),
        cut,
        singles,
        len(sizes),
        len(writer.pages),
        blank_count,
    )
    with open(report_path(input_path), "w", encoding="utf-8") as report_file:
        report_file.write(report)
    print(report)
    return out


def build_report(
    input_path,
    out,
    shot,
    landscape_count,
    portrait_count,
    shot_forced,
    cut,
    singles,
    source_count,
    output_count,
    blank_count,
):
    direction = "左右竖切" if shot == LANDSCAPE else "上下横切，上半边为左页"
    source = "手动指定" if shot_forced else "按多数页面判断"
    lines = [
        f"输入：{input_path}",
        f"输出：{out}",
        f"拍法：{SHOT_NAMES[shot]}，{source}（横向更长 {landscape_count} 页，竖向更长 {portrait_count} 页）",
        f"双页切法：{direction}",
        f"切线：{cut.describe()}",
        f"共 {source_count} 页 -> {output_count} 页，其中补空白页 {blank_count} 张",
        "",
    ]
    if not singles:
        lines.append("单页：无")
    else:
        lines.append(f"单页（不切）共 {len(singles)} 页：")
        lines.append("原页号 | 显示尺寸(mm) | 输出页号")
        for number, width, height, output_no, overridden in singles:
            size = f"{width * MM_PER_POINT:.0f}x{height * MM_PER_POINT:.0f}"
            note = "（手动指定）" if overridden else ""
            lines.append(f"第{number}页 | {size} | 第{output_no}页{note}")
        numbers = ",".join(str(item[0]) for item in singles)
        lines.append("")
        lines.append("单页判断不对时，按原页号改参数后重跑，例如：")
        lines.append(f'python3 book_cut.py "{input_path}" --page {numbers}=single --page 页号=split')
    other = PORTRAIT if shot == LANDSCAPE else LANDSCAPE
    lines.append("")
    lines.append(f"如果整份 PDF 其实是{SHOT_NAMES[other]}，加上 --shot {other} 重跑。")
    return "\n".join(lines) + "\n"


def build_cut_settings(args):
    cut = CutSettings(mode=args.cut)
    for name in ("left_end", "right_start", "auto_start", "auto_step", "auto_max"):
        value = getattr(args, name)
        if value is not None:
            setattr(cut, name, value)
    for name in ("left_end", "right_start", "auto_start", "auto_max"):
        value = getattr(cut, name)
        if not 0 < value < 1:
            raise SystemExit(f"{name} 必须在 0 到 1 之间：{value}")
    return cut


def main(argv=None):
    parser = argparse.ArgumentParser(description="按整份 PDF 的拍法切开双页，并补空白页对齐双面打印。")
    parser.add_argument("pdf", nargs="*", help="要处理的 PDF；不填则处理当前目录下所有 PDF")
    parser.add_argument(
        "--page",
        action="append",
        metavar="页号=处理",
        help="按原页号指定处理方式：split 切开，single 不切。可写 3,5-7=split，可重复",
    )
    parser.add_argument(
        "--shot",
        choices=(LANDSCAPE, PORTRAIT),
        help="指定整份 PDF 的拍法：landscape 横拍（左右竖切），portrait 竖拍（上下横切）。不填则按多数页面判断",
    )
    parser.add_argument("--cut", choices=("fixed", "auto"), default="fixed", help="切线模式，默认 fixed")
    parser.add_argument("--left-end", type=float, help="固定模式：左（上）页截到的位置，默认 0.52")
    parser.add_argument("--right-start", type=float, help="固定模式：右（下）页开始的位置，默认 0.48")
    parser.add_argument("--auto-start", type=float, help="自动模式：第 1 页切线位置，默认 0.50")
    parser.add_argument(
        "--auto-step", type=float, help="自动模式：每页切线移动的比例，默认 0.02/49（第 50 页到 52%%）"
    )
    parser.add_argument("--auto-max", type=float, help="自动模式：切线最多移到的位置，默认 0.60")
    args = parser.parse_args(argv)

    cut = build_cut_settings(args)
    targets = args.pdf or [
        name
        for name in sorted(listdir("."))
        if splitext(name)[1].lower() == ".pdf" and not name.lower().endswith("-print.pdf")
    ]
    if (args.page or args.shot) and len(targets) != 1:
        raise SystemExit("--page 和 --shot 只能在处理单个 PDF 时使用")
    for path in targets:
        process(path, args.page, cut, args.shot)


if __name__ == "__main__":
    sys.exit(main())
