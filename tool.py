# coding=utf-8
import sys
from copy import copy
from os import listdir
from os.path import splitext

from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import RectangleObject

# 单位：point。允许扫描件和标准纸张有少量偏差。
A4_SHORT, A4_LONG = 595.28, 841.89
A3_SHORT, A3_LONG = 841.89, 1190.55
# 左页切线略过中线，右页切线略不到中线，中缝附近的字两边都留住。
LEFT_RATIO = 0.52
RIGHT_RATIO = 0.48


def output_path(input_path):
    base, _ext = splitext(input_path)
    return base + "-print.pdf"


def _size_error(width, height, short, long_side):
    actual_short, actual_long = sorted((width, height))
    return max(abs(actual_short - short) / short, abs(actual_long - long_side) / long_side)


def classify_page(width, height):
    """返回 single、vertical（竖切）或 horizontal（横切）。"""
    if _size_error(width, height, A4_SHORT, A4_LONG) <= 0.08:
        return "single"
    if _size_error(width, height, A3_SHORT, A3_LONG) <= 0.12:
        return "vertical" if width >= height else "horizontal"
    # 非标准尺寸：明显偏宽视为左右双页，明显偏高视为上下双页。
    if width > height * 1.25:
        return "vertical"
    if height > width * 1.9:
        return "horizontal"
    return "single"


def _set_box(page, x0, y0, x1, y1):
    page.mediabox = RectangleObject((x0, y0, x1, y1))


def crop_page(page, kind, side):
    box = page.mediabox
    x0, y0 = float(box.left), float(box.bottom)
    x1, y1 = float(box.right), float(box.top)
    if kind == "vertical":
        width = x1 - x0
        if side == "left":
            x1 = x0 + width * LEFT_RATIO
        else:
            x0 = x0 + width * RIGHT_RATIO
    else:
        height = y1 - y0
        # 竖版双页：上半为左页，下半为右页。
        if side == "left":
            y0 = y0 + height * RIGHT_RATIO
        else:
            y1 = y0 + height * LEFT_RATIO
    _set_box(page, x0, y0, x1, y1)
    return page


def _add_blank_a4(writer):
    writer.add_blank_page(width=A4_SHORT, height=A4_LONG)


def process(input_path):
    reader = PdfReader(input_path)
    writer = PdfWriter()
    out = output_path(input_path)
    log = []

    for index, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        kind = classify_page(width, height)
        if kind == "single":
            writer.add_page(page)
            log.append(f"第{index}页 {width:.0f}x{height:.0f} 单页，不切 -> 输出第{len(writer.pages)}页")
            continue

        # 左页必须是双页（偶数页），右页是紧随其后的单页（奇数页）。
        if len(writer.pages) % 2 == 0:
            _add_blank_a4(writer)
            log.append(f"  补空白A4 -> 输出第{len(writer.pages)}页")
        direction = "竖切" if kind == "vertical" else "横切"
        right_page = copy(page)
        left = crop_page(page, kind, "left")
        right = crop_page(right_page, kind, "right")
        writer.add_page(left)
        left_no = len(writer.pages)
        writer.add_page(right)
        right_no = len(writer.pages)
        log.append(
            f"第{index}页 {width:.0f}x{height:.0f} {direction} -> 左页第{left_no}页（双页），右页第{right_no}页（单页）"
        )

    with open(out, "wb") as output_file:
        writer.write(output_file)
    print("\n".join(log))
    print(f"共 {len(reader.pages)} 页 -> {len(writer.pages)} 页")
    print(out)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = [
            name
            for name in listdir(".")
            if splitext(name)[1].lower() == ".pdf" and not name.lower().endswith("-print.pdf")
        ]
    for path in targets:
        process(path)
