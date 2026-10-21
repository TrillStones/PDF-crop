# coding=utf-8
"""把拍摄或扫描的书页 PDF 切开，并补空白页对齐双面打印。

处理规则：
- 先裁掉每一页四周的空白（和页面边缘颜色一致的部分），再按裁剪后的形状判断。
- 不看纸张尺寸，只看整份 PDF 的拍法。
  大部分页面横向更长（横拍）：横向更长的页面是双页，左右竖切；
  竖向更长的页面是单页，不切。
  大部分页面竖向更长（竖拍）：竖向更长的页面是双页，上下横切，上半边为左页；
  横向更长的页面是单页，不切。
- 切线有两种模式：
  fixed：左（上）页取 0 到 left_end，右（下）页取 right_start 到 100%。
  auto：书越往后翻，左侧拱起越多，切线随原 PDF 页号逐步向右（下）移动。
- 切开后的左页必须落在输出 PDF 的偶数页，右页在紧接着的奇数页。
  位置不对时，先补一张空白页。
- 每一页最后等比缩放到 A4，横向的页面用横向 A4，竖向的用竖向 A4，居中放置。
  内容方向保持不变，不做旋转。

裁剪空白需要 ImageMagick（magick 命令）和 Ghostscript；找不到时跳过裁剪并给出提示。

用法：
    python3 book_cut.py 文件.pdf
    python3 book_cut.py 文件.pdf --cut fixed --left-end 0.53 --right-start 0.47
    python3 book_cut.py 文件.pdf --cut auto --auto-step 0.0004
    python3 book_cut.py 文件.pdf --page 2,7=single --page 5=split
    python3 book_cut.py 文件.pdf --shot portrait
    python3 book_cut.py 文件.pdf --no-trim --page-size original
    python3 book_cut.py              # 处理当前目录下所有 PDF
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from copy import copy
from dataclasses import dataclass
from os import listdir
from os.path import splitext

from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import DecodedStreamObject, NameObject, NumberObject, RectangleObject

MM_PER_POINT = 25.4 / 72
A4_SHORT, A4_LONG = 595.28, 841.89

# 测量空白用的渲染精度和颜色容差。
TRIM_DENSITY = 36
TRIM_FUZZ = "12%"
# 渲染时一批处理的页数，避免长文档一次占用太多内存。
TRIM_BATCH = 40
# 裁剪范围四周各放宽的像素数，避免把内容边缘裁掉。
TRIM_PADDING_PX = 1

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


def _full_region(page):
    width, height = display_size(page)
    return (0.0, 0.0, width, height)


def _region_size(region):
    x0, y0, x1, y1 = region
    return x1 - x0, y1 - y0


def measure_content(input_path, pages):
    """返回每一页去掉四周空白后的内容范围。

    范围用显示坐标表示：原点在显示画面左下角，单位 point，(x0, y0, x1, y1)。
    无法测量的页面返回整页。第二个返回值是没法裁剪时的说明。
    """
    regions = [_full_region(page) for page in pages]
    if not shutil.which("magick"):
        return regions, "未找到 ImageMagick（magick 命令），没有裁剪空白"
    with tempfile.TemporaryDirectory() as work_dir:
        # 文件名里的 [ ] 会被 ImageMagick 当成页码，统一换成临时名字。
        link = os.path.join(work_dir, "input.pdf")
        try:
            os.symlink(os.path.abspath(input_path), link)
        except OSError:
            shutil.copyfile(input_path, link)
        for start in range(0, len(pages), TRIM_BATCH):
            end = min(len(pages), start + TRIM_BATCH) - 1
            command = [
                "magick",
                "-define",
                "pdf:use-cropbox=true",
                "-density",
                str(TRIM_DENSITY),
                f"{link}[{start}-{end}]",
                "-fuzz",
                TRIM_FUZZ,
                "-format",
                "%w %h %@\n",
                "info:",
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                return [_full_region(page) for page in pages], "ImageMagick 渲染失败，没有裁剪空白"
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            for offset, line in enumerate(lines):
                index = start + offset
                if index >= len(pages):
                    break
                trimmed = _parse_trim(line, display_size(pages[index]))
                if trimmed:
                    regions[index] = trimmed
    return regions, None


def _parse_trim(line, page_size):
    match = re.match(r"(\d+) (\d+) (\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line.strip())
    if not match:
        return None
    image_w, image_h, trim_w, trim_h, left, top = (int(value) for value in match.groups())
    width, height = page_size
    # 渲染结果和显示方向对不上时不裁，避免裁错地方。
    if image_w <= 0 or image_h <= 0 or (image_w > image_h) != (width > height):
        return None
    if trim_w <= 1 or trim_h <= 1:
        return None
    left = max(0, left - TRIM_PADDING_PX)
    top = max(0, top - TRIM_PADDING_PX)
    right = min(image_w, left + trim_w + 2 * TRIM_PADDING_PX)
    bottom = min(image_h, top + trim_h + 2 * TRIM_PADDING_PX)
    scale_x, scale_y = width / image_w, height / image_h
    return (left * scale_x, height - bottom * scale_y, right * scale_x, height - top * scale_y)


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


def _display_matrix(page):
    """页面坐标到显示坐标的变换矩阵 (a, b, c, d, e, f)，已把 /Rotate 算进去。"""
    x0, y0, x1, y1 = _base_box(page)
    rotation = _rotation(page)
    if rotation == 90:
        return (0, -1, 1, 0, -y0, x1)
    if rotation == 180:
        return (-1, 0, 0, -1, x1, y1)
    if rotation == 270:
        return (0, 1, -1, 0, y1, -x0)
    return (1, 0, 0, 1, -x0, -y0)


def split_regions(region, shot, left_end, right_start):
    """返回 (左页范围, 右页范围)。横拍时左右竖切；竖拍时上下横切，上半边为左页。"""
    x0, y0, x1, y1 = region
    width, height = x1 - x0, y1 - y0
    if shot == LANDSCAPE:
        return (x0, y0, x0 + width * left_end, y1), (x0 + width * right_start, y0, x1, y1)
    return (x0, y1 - height * left_end, x1, y1), (x0, y0, x1, y1 - height * right_start)


def target_size(region, page_size):
    width, height = _region_size(region)
    if page_size == "original":
        return width, height
    if width > height:
        return A4_LONG, A4_SHORT
    return A4_SHORT, A4_LONG


def place_region(page, region, target):
    """返回一个新页面：只显示 region 范围，等比缩放后居中放进 target 大小的页面。"""
    out = copy(page)
    x0, y0, x1, y1 = region
    width, height = x1 - x0, y1 - y0
    target_w, target_h = target
    scale = min(target_w / width, target_h / height)
    offset_x = (target_w - width * scale) / 2
    offset_y = (target_h - height * scale) / 2
    a, b, c, d, e, f = _display_matrix(page)
    matrix = (
        a * scale,
        b * scale,
        c * scale,
        d * scale,
        (e - x0) * scale + offset_x,
        (f - y0) * scale + offset_y,
    )
    content = page.get_contents()
    data = content.get_data() if content is not None else b""
    # 先按目标位置裁切，再变换原内容，裁出范围外的部分不会露出来。
    prefix = (
        f"q {offset_x:.4f} {offset_y:.4f} {width * scale:.4f} {height * scale:.4f} re W n "
        + " ".join(f"{value:.6f}" for value in matrix)
        + " cm\n"
    ).encode("ascii")
    stream = DecodedStreamObject()
    stream.set_data(prefix + data + b"\nQ\n")
    out[NameObject("/Contents")] = stream
    box = RectangleObject((0, 0, target_w, target_h))
    out.mediabox = box
    out.cropbox = box
    for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
        if name in out:
            del out[name]
    out[NameObject("/Rotate")] = NumberObject(0)
    return out


@dataclass
class OutputSettings:
    trim: bool = True
    # a4：等比缩放进 A4，横竖跟随页面自身；original：保持裁剪后的大小。
    page_size: str = "a4"


def process(input_path, override_values=None, cut=None, forced_shot=None, output=None):
    cut = cut or CutSettings()
    output = output or OutputSettings()
    reader = PdfReader(input_path)
    pages = list(reader.pages)
    if output.trim:
        regions, trim_note = measure_content(input_path, pages)
    else:
        regions, trim_note = [_full_region(page) for page in pages], "已关闭（--no-trim）"
    full_sizes = [display_size(page) for page in pages]
    trimmed_count = sum(
        1
        for region, (width, height) in zip(regions, full_sizes)
        if _region_size(region)[0] < width * 0.99 or _region_size(region)[1] < height * 0.99
    )
    sizes = [_region_size(region) for region in regions]
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
    for number, (page, action, region) in enumerate(zip(pages, actions, regions), start=1):
        if action == SINGLE:
            target = target_size(region, output.page_size)
            writer.add_page(place_region(page, region, target))
            width, height = _region_size(region)
            singles.append((number, width, height, target, len(writer.pages), number in overrides))
            continue

        left_end, right_start = cut.split_fractions(number)
        left_region, right_region = split_regions(region, shot, left_end, right_start)
        left_target = target_size(left_region, output.page_size)
        if (len(writer.pages) + 1) % 2 == 1:
            writer.add_blank_page(width=left_target[0], height=left_target[1])
            blank_count += 1
        writer.add_page(place_region(page, left_region, left_target))
        writer.add_page(place_region(page, right_region, target_size(right_region, output.page_size)))

    out = output_path(input_path)
    with open(out, "wb") as output_file:
        writer.write(output_file)

    if output.trim and trim_note is None:
        trim_note = f"已开启，{trimmed_count} 页裁掉了四周空白"
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
        trim_note,
        output.page_size,
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
    trim_note,
    page_size,
):
    direction = "左右竖切" if shot == LANDSCAPE else "上下横切，上半边为左页"
    source = "手动指定" if shot_forced else "按多数页面判断"
    if page_size == "a4":
        size_text = "统一等比缩放到 A4，横竖跟随页面自身方向"
    else:
        size_text = "保持裁剪后的原始大小"
    lines = [
        f"输入：{input_path}",
        f"输出：{out}",
        f"裁剪空白：{trim_note}",
        f"拍法：{SHOT_NAMES[shot]}，{source}（横向更长 {landscape_count} 页，竖向更长 {portrait_count} 页）",
        f"双页切法：{direction}",
        f"切线：{cut.describe()}",
        f"输出页面：{size_text}",
        f"共 {source_count} 页 -> {output_count} 页，其中补空白页 {blank_count} 张",
        "",
    ]
    if not singles:
        lines.append("单页：无")
    else:
        lines.append(f"单页（不切）共 {len(singles)} 页：")
        lines.append("原页号 | 裁剪后尺寸(mm) | 输出尺寸(mm) | 输出页号")
        for number, width, height, target, output_no, overridden in singles:
            size = f"{width * MM_PER_POINT:.0f}x{height * MM_PER_POINT:.0f}"
            out_size = f"{target[0] * MM_PER_POINT:.0f}x{target[1] * MM_PER_POINT:.0f}"
            note = "（手动指定）" if overridden else ""
            lines.append(f"第{number}页 | {size} | {out_size} | 第{output_no}页{note}")
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
    parser.add_argument("--no-trim", action="store_true", help="不裁剪页面四周的空白")
    parser.add_argument(
        "--page-size",
        choices=("a4", "original"),
        default="a4",
        help="输出页面大小：a4 等比缩放到 A4（默认），original 保持裁剪后的大小",
    )
    args = parser.parse_args(argv)

    cut = build_cut_settings(args)
    output = OutputSettings(trim=not args.no_trim, page_size=args.page_size)
    targets = args.pdf or [
        name
        for name in sorted(listdir("."))
        if splitext(name)[1].lower() == ".pdf" and not name.lower().endswith("-print.pdf")
    ]
    if (args.page or args.shot) and len(targets) != 1:
        raise SystemExit("--page 和 --shot 只能在处理单个 PDF 时使用")
    for path in targets:
        process(path, args.page, cut, args.shot, output)


if __name__ == "__main__":
    sys.exit(main())
