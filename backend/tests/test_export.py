"""Excel 导出测试：结构合法、日期是文本、金额数值、列宽存在。"""

from __future__ import annotations

import io
import unittest
import xml.etree.ElementTree as ET
import zipfile

from bankagent import xlsx


class TestXlsx(unittest.TestCase):
    def _build(self):
        data = xlsx.build(
            ["日期", "金额", "对手方"],
            [["2026-10-12", 300.0, "张三"], ["2026-09-02", 15.5, "网易云音乐"]],
            widths=[13, 12, 16],
        )
        return data, zipfile.ZipFile(io.BytesIO(data))

    def test_workbook_structure(self):
        _, zf = self._build()
        names = set(zf.namelist())
        for part in ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                     "xl/styles.xml", "xl/worksheets/sheet1.xml"]:
            self.assertIn(part, names)
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn('width="13"', sheet)
        self.assertIn("2026-10-12", sheet)
        self.assertIn('dimension ref="A1:C3"', sheet)
        ET.fromstring(sheet)  # XML 合法

    def test_dates_are_text_and_amounts_numeric(self):
        _, zf = self._build()
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        # 日期单元格是 inlineStr（文本），不会被 Excel 自动转日期
        self.assertIn('t="inlineStr"', sheet)
        self.assertIn('s="2"><v>300.0</v>', sheet)


if __name__ == "__main__":
    unittest.main()
