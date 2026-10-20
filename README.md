# PDF-crop

Split pages of pdf files down the middle, useful for scanning work. For more information, please see [the project website](https://blog.baoyukun.win/%E6%8A%80%E6%9C%AF/%E5%B7%A5%E5%85%B7/crop-pdf/).

基于PyPDF2，一个沿中缝批量裁剪PDF的小工具，一页变两页，方便双页快速扫描的后期处理。

## 使用说明（book_cut.py）

`book_cut.py` 是当前推荐使用的版本。它把拍摄或扫描的书页 PDF 中的双页切开，并自动补空白页，让切开后的两页在双面打印、对页查看时仍然连在一起。

### 环境准备

需要 Python 3.7 及以上，以及 PyPDF2 3.x：

```bash
pip3 install "PyPDF2>=3,<4"
```

### 快速开始

**基本用法**

在仓库根目录（`book_cut.py` 所在的目录）下运行：

```bash
python3 book_cut.py "Work Dir/20261018/书名.pdf"
```

文件名里有空格、括号等符号时，要用英文双引号把路径括起来。

每处理一个 PDF，会在它旁边生成两个文件，原文件不会被修改：

| 文件 | 内容 |
|---|---|
| `书名-print.pdf` | 切好、补好空白页的 PDF |
| `书名-print-report.txt` | 处理报告，内容和命令行输出一样 |

再次运行会覆盖这两个文件。

**推荐的使用流程**

1. 先不加任何参数跑一遍：

   ```bash
   python3 book_cut.py "Work Dir/20261018/书名.pdf"
   ```

2. 看命令行输出或 `-print-report.txt`，确认三件事：
   - “拍法”是否正确：横拍会左右竖切，竖拍会上下横切。
   - “单页（不切）”列出的页面，是否确实是不该切的单页，例如封面、封底、扉页。
   - 补了几张空白页，总页数是否合理。
3. 打开 `-print.pdf`，用阅读器的“双页 + 封面单独显示”模式翻一遍。每张原来的双页，应该左半边在左、右半边在右地并排出现。
4. 哪里不对，就按原 PDF 页号加参数重跑。具体写法见下面的常用案例和“手动指定怎么切”。

**常用案例**

| 情况 | 命令 |
|---|---|
| 书摊开横着拍或扫描，大部分页面是横的 | `python3 book_cut.py "书名.pdf"` |
| 手机竖着拍，上下两页在一张照片里，大部分页面是竖的 | `python3 book_cut.py "书名.pdf"` |
| 不切的单页比双页还多，拍法判反了 | `python3 book_cut.py "书名.pdf" --shot portrait`（或 `landscape`） |
| 封面、封底等几页被误切了 | `python3 book_cut.py "书名.pdf" --page 1,2,27=single` |
| 某几页应该切但没切 | `python3 book_cut.py "书名.pdf" --page 9,15-16=split` |
| 中缝附近的字被切掉了，两边要多留一点 | `python3 book_cut.py "书名.pdf" --left-end 0.55 --right-start 0.45` |
| 书比较厚，越往后中缝越偏 | `python3 book_cut.py "书名.pdf" --cut auto` |
| 用自动模式，并加快切线移动 | `python3 book_cut.py "书名.pdf" --cut auto --auto-step 0.0008` |

多个参数可以一起用，例如手动指定拍法、修正个别页，同时用自动切线：

```bash
python3 book_cut.py "书名.pdf" --shot portrait --page 1=single --page 9=split --cut auto
```

**批量处理**

```bash
# 一次处理多个指定的 PDF
python3 book_cut.py "Work Dir/20261018/书名A.pdf" "Work Dir/20261019/书名B.pdf"

# 处理某个文件夹里的所有 PDF：先进入该文件夹，不带文件名运行
cd "Work Dir/20261018"
python3 ../../book_cut.py
```

批量处理时要注意：

- 不带文件名时，只处理当前文件夹里的 PDF，不会进入子文件夹，并且会自动跳过已经生成的 `-print.pdf`。
- 直接写出多个文件名或用 `*.pdf` 通配时，不会自动跳过 `-print.pdf`，要避免把上次的结果再切一次。
- `--shot` 和 `--page` 只能在一次处理一个 PDF 时使用。切线参数（`--cut`、`--left-end` 等）可以用于批量处理，对每个文件都生效。

### 处理规则

**1. 判断整份 PDF 是横拍还是竖拍**

程序不看纸张是 A3 还是 A4，只比较每一页在阅读器里显示出来的宽和高。页面带旋转（`/Rotate`）时，按旋转后的方向算。

| 多数页面 | 判定为 | 双页（和多数页面方向相同） | 单页（方向相反，不切） |
|---|---|---|---|
| 横向更长（宽 > 高） | 横拍 | 左右竖切：左半边为左页，右半边为右页 | 竖向更长的页面 |
| 竖向更长（高 > 宽） | 竖拍 | 上下横切：上半边为左页，下半边为右页 | 横向更长的页面 |

横向和竖向页面一样多时，按横拍处理。宽高完全相等的页面当作单页。

> 这个规则假设双页比单页多。如果一份 PDF 里不切的单页反而更多，会判反，这时请用 `--shot` 手动指定，见下文。

**2. 切线位置**

切线有两种模式，横切和竖切都适用。竖切时“左页”指左半边，横切时指上半边。

- **固定模式（默认）**：左（上）页取 0–52%，右（下）页取 48–100%。中间 4% 两边都保留，避免中缝附近的文字被切断。
- **自动模式**：书越往后翻，左侧拱起越多，中缝会逐渐右移。切线按原 PDF 页号逐页移动，默认第 1 页在 50%，第 50 页在 52%，最多移到 60%。自动模式下左右两页在同一条线切开，不留重叠。

**3. 补空白页**

切开后的左页必须落在输出 PDF 的偶数页，右页落在紧接着的奇数页。这样用 A4 双面打印或用阅读器对页查看时，同一张双页的两半是连在一起的。

程序从前往后逐页处理，每遇到一张双页都重新检查一次：如果左页会落到奇数页，就先补一张空白页，大小和这张双页切出来的左页一样。单页不检查，直接排进下一个位置。因此 PDF 中间单页、双页多次交替出现时，也会在需要的地方多次补空白页。

**4. 页面方向保持原样**

程序只负责切开和补空白页，不会把侧着拍的页面转正，需要时请在阅读器或其他工具里手动旋转。

### 处理报告

报告只详细列出被当作单页、没有切开的页面，双页只给汇总信息。例如：

```text
输入：Work Dir/20261019/学琴之路-横向版02.pdf
输出：Work Dir/20261019/学琴之路-横向版02-print.pdf
拍法：竖拍，手动指定（横向更长 3 页，竖向更长 2 页）
双页切法：上下横切，上半边为左页
切线：固定：左（上）页 0–52%，右（下）页 48%–100%
共 5 页 -> 9 页，其中补空白页 2 张

单页（不切）共 3 页：
原页号 | 显示尺寸(mm) | 输出页号
第1页 | 297x210 | 第1页
第2页 | 297x210 | 第2页
第4页 | 297x210 | 第6页
```

报告最后会给出一条可以直接复制修改的重跑命令。建议的流程是：先自动跑一遍，看报告和 `-print.pdf`；哪里不对，按原页号手动指定后重跑。

### 手动指定怎么切

自动判断不对时，可以用下面两个参数修正。它们只能在一次处理一个 PDF 时使用。

**1. `--shot`：指定整份 PDF 的拍法，也就是切的方向**

| 写法 | 含义 |
|---|---|
| `--shot landscape` | 横拍：横向更长的页面左右竖切，竖向更长的页面不切 |
| `--shot portrait` | 竖拍：竖向更长的页面上下横切，横向更长的页面不切 |

```bash
# 学琴之路-横向版02：横向 3 页是单页，竖向 2 页才是要横切的双页，
# 按多数判断会判成横拍，需要手动指定为竖拍
python3 book_cut.py "Work Dir/20261019/学琴之路-横向版02.pdf" --shot portrait
```

**2. `--page`：按原 PDF 页号指定某些页切还是不切**

格式是 `--page 页号=处理方式`：

- 处理方式只有两种：`split` 表示切开，`single` 表示不切。
- 页号是原 PDF 的页号，从 1 开始，可以写单页 `3`、多页 `3,5,9`、范围 `5-8`，也可以混写 `1,3,5-8`。
- `--page` 可以写多次。同一页写了多次时，以最后一次为准。
- `--page` 只决定切不切，切的方向仍由拍法决定。需要换方向时配合 `--shot` 使用。

```bash
# 第 1、2 页和最后一页（第 27 页）不切
python3 book_cut.py "书名.pdf" --page 1,2,27=single

# 第 9 页被误判为单页，强制切开
python3 book_cut.py "书名.pdf" --page 9=split

# 第 3、5 到 8 页切开，第 4 页不切
python3 book_cut.py "书名.pdf" --page 3,5-8=split --page 4=single

# 先指定拍法，再单独修正个别页
python3 book_cut.py "书名.pdf" --shot portrait --page 2=single --page 10=split
```

手动指定的单页，在报告里会标注“（手动指定）”。

### 调整切线位置

**固定模式**

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--left-end` | 左（上）页截到的位置 | `0.52` |
| `--right-start` | 右（下）页开始的位置 | `0.48` |

```bash
# 中缝附近内容多，左右各多留一点：左页取 0–54%，右页取 46–100%
python3 book_cut.py "书名.pdf" --left-end 0.54 --right-start 0.46
```

**自动模式**

| 参数 | 含义 | 默认值 |
|---|---|---|
| `--cut auto` | 打开自动模式 | 默认是 `fixed` |
| `--auto-start` | 原 PDF 第 1 页的切线位置 | `0.50` |
| `--auto-step` | 每往后一页，切线移动的比例 | `0.02/49`，约 `0.000408`，即第 50 页到 52% |
| `--auto-max` | 切线最多移到的位置 | `0.60` |

第 N 页的切线位置 = `auto-start + auto-step × (N − 1)`，超过 `auto-max` 时取 `auto-max`。

```bash
# 默认速度：第 1 页 50%，第 50 页 52%
python3 book_cut.py "书名.pdf" --cut auto

# 书比较厚，移动快一倍：第 1 页 50%，第 50 页约 54%
python3 book_cut.py "书名.pdf" --cut auto --auto-step 0.0008

# 从 49% 开始，最多移到 55%
python3 book_cut.py "书名.pdf" --cut auto --auto-start 0.49 --auto-max 0.55
```

除 `--auto-step` 外，这些位置参数都必须在 0 到 1 之间。

### 参数一览

```text
python3 book_cut.py [PDF ...] [--shot landscape|portrait] [--page 页号=split|single ...]
                    [--cut fixed|auto] [--left-end 比例] [--right-start 比例]
                    [--auto-start 比例] [--auto-step 比例] [--auto-max 比例]
```

运行 `python3 book_cut.py -h` 可以查看命令行帮助。

> 仓库里的 `tool.exe` 和下文“我的方案”介绍的是最早的版本，会把每一页都从中间切成两页。`tool.py` 是之后按 A3/A4 尺寸判断的过渡版本。两者都已被 `book_cut.py` 取代。

## 应用场景

我们经常会扫描书籍到电子设备中，但一页页地扫描不仅效率太低，而且书籍装订中缝旁扫描效果欠佳，尤其是厚书。一个很自然的想法是，将书籍摊开，而后用机器上盖压实，一次扫描两页，最后再将扫描出的PDF沿中缝裁剪开来，一页变两页。

比如拿A4大小的书籍来说，就是按A3大小扫描进电脑，而后将A3的长边从中切开。

首先当然想到PDF的处理神器：**Adobe Acrobat X Pro**。如果去`Tools`-->`Pages`-->`Crop`看一看，或者在页面预览中选中一页单击右键、点击`Crop Pages`，通过设置margin的值将所有Page裁去一半是很容易做到的。然而问题是： **原页面的另一半将丢失！** 倘若将原件复制一份，对原件进行左切割，而对副本进行右切割，最后将两者merge也是可以的，然而新的问题来了： **如何把两份PDF交叉merge，即A-1P, B-1P, A-2P, B-2P...？** 正是因为没有找到第二个问题的解决办法，不得不寻求其他解决方案。

然后想到，PDF、图片和音视频文件的在线解决方案很多，果然找到了一个：[**Sejda**](https://www.sejda.com/)。在它的[`Split PDF pages down the middle`](https://www.sejda.com/split-pdf-down-the-middle)服务中，我们可以达成目的。然而这个工具有两个缺点：

1. 次数限制：免费版每天只可以裁剪3次
2. 中缝内容缺失：由于扫描摆放或者文字本身太靠近中缝等原因，严格由中间切开会导致 **_文字断裂_**

第一个缺点还可以勉强接受（*我们可以将要裁剪的文件合并到一起集中处理*），第二个缺点是致命的，无论对于阅读，还是对于再打印。因此我们希望： **切割得到左页面时，切割线比中线稍微向右去一点；反之同理。**

之后，我按照`Crop pdf pages`、`Split pdf down the middle`等关键字在Google上搜索，都没能得到简洁满意的答案。在GitHub上，也有不少项目关注到了这一需求，然而没找到可以直接使用的产品，大多给出了编程的代码块。

于是，我最终决定：自己动手写一个，一劳永逸！

>值得注意的是，很多有 *split* 字眼的解决方案，都是在分割页与页，即将一个PDF文档分成几个PDF文档。而我们需要的是分割一页得到两页的工具，不要混淆。

## 我的方案

这样的小工具，用Python写最简单了，基于PyPDF2（*a pure-python PDF library*）。

要达成我们的目的，需要深入了解库中的[`RectangleObject class`](https://pythonhosted.org/PyPDF2/RectangleObject.html#PyPDF2.generic.RectangleObject)，但是官方文档太简单，经过探究，下面用图例的方式对其作出详细说明：

```
LL(lowerLeft)     .          UL(upperLeft)
                  |
  ----------------.-------------> Y
  |               |
  |               .
  |               |
  |               .
  |               |
  |               .
  |               |
  |               .
  |               |
  V  X            .
                  |  
LR(lowerRight)    .          UR(upperRight)
                  |          
```

这是我们站在复印机前俯视机器时的效果，书籍竖放摊开，因此 **Y轴方向是A3长边**，我们希望分割的就是Y轴。而文档中所谓的 *upper*、*left* 等方向，是从观察者左侧俯视的结果，或者理解为 **X轴正向为右，Y轴正向为上**。

理解了文档，代码就不难写出了，代码中给出了相应的注释：

```python
#coding=utf-8
from PyPDF2 import PdfFileWriter, PdfFileReader
from copy import copy
from os import listdir

def op(inputFilePath):
    outputFilePath = inputFilePath[:-4]+'-print.pdf'

    inputFile = open(inputFilePath, 'rb')
    inputStream = PdfFileReader(inputFile)
    outputStream = PdfFileWriter()

    for page in [inputStream.getPage(i) for i in range(inputStream.getNumPages())]:
        pageLeft = page
	#注意：简单的assignment会导致左、右切割操作同一页面对象
        pageRight = copy(page)
	#切割得到左页面时，切割线比中线稍微向右去一点
        pageLeft.mediaBox.upperRight = (pageLeft.mediaBox.getUpperRight_x(), pageLeft.mediaBox.getUpperRight_y() * 52/100)
	#切割得到右页面时，切割线比中线稍微向左去一点
        pageRight.mediaBox.lowerRight = (pageRight.mediaBox.getLowerRight_x(), pageRight.mediaBox.getUpperRight_y() * 48/100)
        outputStream.addPage(pageLeft)
        outputStream.addPage(pageRight)

    with open(outputFilePath, "wb") as outputFile:
        outputStream.write(outputFile)

    inputFile.close()

#批量处理当前目录下的所有PDF文件
for file in listdir('.'):
    if file[-4:]=='.pdf' or file[-4:]=='.PDF':
        op(file)
```

最后，可以下载使用发布好的[Windows版本工具](https://raw.githubusercontent.com/baoyukun/PDF-crop/master/tool.exe)，只需轻轻双击，当前目录下的所有PDF文档全搞定！工具只会生成切割后新的`-print.pdf`后缀文件，不会删除或者覆盖原文件，还不快来试试呀(｡･ω･｡)

>对于非双页扫描的普通PDF文件，切割效果是上下分割，这是正常的，原因就藏在上面的图示里咯！
