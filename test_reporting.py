import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

os.environ["EMPLOYEE_VISITS_DB"] = os.path.join(tempfile.mkdtemp(), "report_test.db")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import reporting

db.init_db()
passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {label}")
    else:
        failed += 1
        print(f"FAIL  {label}")


db.add_employee("E100", "Rafiq Islam", "Officer", "01700000000", "", "rafiq@x.com", 2)
db.add_employee("E200", "Sultana Begum", "GM", "01800000000", "", "", 0)
for i in range(1, 3):
    db.add_visit("E100", f"Country {i}", f"Purpose title {i}", f"Long detail text {i} " * 12, f"2026-0{i}-15")
for i in range(1, 31):
    month = (i % 12) + 1
    db.add_visit("E200", "UAE", f"Deal {i}", "notes", f"2026-{month:02d}-15")
db.add_employee("E300", "Nobody Visits", "Clerk", max_visits=2)

out = tempfile.mkdtemp()
csv_path = os.path.join(out, "r.csv")
xlsx_path = os.path.join(out, "r.xlsx")
pdf_path = os.path.join(out, "r.pdf")

reporting.export_csv(csv_path, "2026")
reporting.export_xlsx(xlsx_path, "2026")
reporting.export_pdf(pdf_path, "2026")

db.add_employee("E400", "রফিক ইসলাম", "কর্মকর্তা", max_visits=2)
db.add_visit("E400", "ভারত", "প্রশিক্ষণ সফর", "বিস্তারিত বিবরণ", "2026-02-20")
pdf_unicode_path = os.path.join(out, "r_unicode.pdf")
reporting.export_pdf(pdf_unicode_path, "2026")

with open(pdf_unicode_path, "rb") as f:
    udata = f.read()
check("unicode pdf embeds TrueType (FontFile2)", b"/FontFile2" in udata)
check("unicode pdf uses Identity-H encoding", b"/Identity-H" in udata)
check("unicode pdf draws CID hex text", re.search(rb"<[0-9A-F]{8}> Tj", udata) is not None)
check("unicode pdf has CIDFontType2", b"/CIDFontType2" in udata)
check("unicode pdf W array present", b"/W [" in udata)

check("csv created", os.path.getsize(csv_path) > 100)
check("xlsx created", os.path.getsize(xlsx_path) > 500)
check("pdf created", os.path.getsize(pdf_path) > 800)

import csv as _csv

with open(csv_path, encoding="utf-8-sig", newline="") as f:
    rows = list(_csv.reader(f))
check("csv has stats header", any("YEARLY EMPLOYEE STATISTICS" in r for r in rows if r))
check("csv has details header", any("VISIT DETAILS" in r for r in rows if r))
det_start = next(i for i, r in enumerate(rows) if r and "VISIT DETAILS" in r[0])
detail_rows = [r for r in rows[det_start + 2:] if r]
check("csv has E100 visits (2)", sum(1 for r in detail_rows if r[0] == "E100") == 2)
check("csv has E200 visits (30)", sum(1 for r in detail_rows if r[0] == "E200") == 30)
check("csv skips zero-visit employee", not any(r and "E300" in r[0] for r in detail_rows))

with zipfile.ZipFile(xlsx_path) as z:
    names = set(z.namelist())
    expected = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"}
    check("xlsx contains all OOXML parts", expected.issubset(names))
    for part in ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                 "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"]:
        try:
            ET.fromstring(z.read(part))
            ok = True
        except ET.ParseError:
            ok = False
        check(f"xlsx XML well-formed: {part}", ok)

    sheet_xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    col_widths = {int(m.group(1)): float(m.group(2)) for m in re.finditer(r'<col min="(\d+)" max="\d+" width="([\d.]+)"', sheet_xml)}
    check("xlsx has auto-width columns", len(col_widths) >= 6)
    purpose_detail_expected = max(len("Purpose Details"), len("Long detail text 1 " * 12))
    check("xlsx widest column tracks longest data (Purpose Details)",
          any(w >= min(purpose_detail_expected * 1.1 + 2, 55) - 0.01 for w in col_widths.values()))
    check("xlsx narrow ID column stays small", col_widths.get(1, 99) <= 16)
    check("xlsx Times Max Reached sized from its header", col_widths.get(10, 0) >= len("Times Max Reached") * 1.1 + 1.9)

with open(pdf_path, "rb") as f:
    data = f.read()
check("pdf starts with %PDF", data.startswith(b"%PDF-1.4"))
check("pdf ends with %%EOF", data.rstrip().endswith(b"%%EOF"))
page_count = data.count(b"/Type /Page ")
check("pdf has pages (2+, long report)", page_count >= 2)
check("pdf xref present", b"\nxref\n" in data)

chunks = re.split(rb"(\d+) 0 obj\r?\n", data)[1:]
objs = {int(chunks[i]): chunks[i + 1] for i in range(0, len(chunks) - 1, 2)}
kids = [int(t) for t in re.findall(rb"(\d+) 0 R", objs[2])]
check("pdf /Kids references exist", all(k in objs for k in kids) and len(kids) >= 2)
check("pdf /Kids are real page objects", all(b"/Type /Page " in objs[k] for k in kids))
streams_ok = True
for k in kids:
    m = re.search(rb"/Contents (\d+) 0 R", objs[k])
    if not m or int(m.group(1)) not in objs:
        streams_ok = False
        break
    if b"Tj" not in objs[int(m.group(1))]:
        streams_ok = False
        break
check("pdf every page has content stream with text", streams_ok)
first_stream_id = int(re.search(rb"/Contents (\d+) 0 R", objs[kids[0]]).group(1))
check("pdf page 1 contains the report title", b"Abroad Visit Report" in objs[first_stream_id])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
