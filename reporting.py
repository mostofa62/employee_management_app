import csv
import os
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

import uharfbuzz as hb

import db

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _status_fields(limit, used, times_reached):
    if limit == 0:
        return "Unlimited", "Unlimited visits", "-"
    if used >= limit:
        return "0", "MAX REACHED - blocked", str(times_reached)
    return str(limit - used), f"{limit - used} visit(s) left", str(times_reached)


def build_report(year):
    year = str(year)
    stats = []
    for r in db.summary(year):
        limit = r["max_visits"]
        used = r["used_this_year"]
        remaining, status, reached = _status_fields(limit, used, r["times_max_reached"])
        stats.append({
            "emp_id": r["emp_id"],
            "name": r["name"],
            "designation": r["designation"],
            "emp_type": r["emp_type"] or "",
            "project": r["project_name"] or "",
            "limit_txt": "Unlimited" if limit == 0 else str(limit),
            "used": used,
            "remaining": remaining,
            "status": status,
            "reached": reached,
        })
    details = []
    for emp in stats:
        visits = db.list_visits(year=year, emp_id=emp["emp_id"])
        details.append({
            "emp_id": emp["emp_id"],
            "name": emp["name"],
            "designation": emp["designation"],
            "visits": [
                {
                    "date": v["visit_date"],
                    "country": v["country"],
                    "title": v["purpose_title"],
                    "detail": v["purpose_detail"],
                }
                for v in visits
            ],
        })
    blocked = sum(1 for s in stats if s["status"].startswith("MAX REACHED"))
    return {
        "year": year,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_employees": len(stats),
        "blocked_count": blocked,
        "stats": stats,
        "details": details,
    }


SUMMARY_HEADERS = ["ID", "Name", "Designation", "Type", "Project", "Yearly Limit",
                   "Visits Used", "Remaining", "Status", "Times Max Reached"]
DETAIL_HEADERS = ["Employee ID", "Employee Name", "Date", "Country", "Purpose Title", "Purpose Details"]


def _summary_row(s):
    return [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
            s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]


def export_csv(path, year):
    rep = build_report(year)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([f"Abroad Visit Report - Year {rep['year']}"])
        w.writerow([f"Generated: {rep['generated']}",
                    f"Total employees: {rep['total_employees']}",
                    f"Reached yearly limit: {rep['blocked_count']}"])
        w.writerow([])
        w.writerow(["YEARLY EMPLOYEE STATISTICS"])
        w.writerow(SUMMARY_HEADERS)
        for s in rep["stats"]:
            w.writerow(_summary_row(s))
        w.writerow([])
        w.writerow(["VISIT DETAILS"])
        w.writerow(DETAIL_HEADERS)
        for d in rep["details"]:
            if not d["visits"]:
                w.writerow([d["emp_id"], d["name"], f"(no visits recorded in {year})", "", "", ""])
                continue
            for v in d["visits"]:
                w.writerow([d["emp_id"], d["name"], v["date"], v["country"], v["title"], v["detail"]])


def _clean_xml(text):
    return _xml_escape(_ILLEGAL_XML.sub("", str(text)))


def _col_letter(n):
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _disp_len(value):
    width = 0.0
    for ch in _ILLEGAL_XML.sub("", str(value)):
        code = ord(ch)
        if code > 0x1100:
            width += 2
        elif code > 126:
            width += 1.7
        else:
            width += 1
    return int(round(width))


def export_xlsx(path, year):
    rep = build_report(year)

    def cell(ref, value, style=None, numeric=False):
        s_attr = f' s="{style}"' if style is not None else ""
        if numeric and isinstance(value, (int, float)):
            return f'<c r="{ref}"{s_attr}><v>{value}</v></c>'
        return f'<c r="{ref}"{s_attr} t="inlineStr"><is><t xml:space="preserve">{_clean_xml(value)}</t></is></c>'

    sheet_rows = []
    width_map = {}

    def add_row(cells, style=None, numeric_cols=(), fit=True):
        idx = len(sheet_rows) + 1
        parts = []
        for ci, value in enumerate(cells, start=1):
            st = style if style is not None else None
            parts.append(cell(f"{_col_letter(ci)}{idx}", value, st, numeric=ci in numeric_cols))
            if fit and str(value).strip():
                ln = _disp_len(value)
                if ln > width_map.get(ci, 0):
                    width_map[ci] = ln
        sheet_rows.append(f'<row r="{idx}">' + "".join(parts) + "</row>")

    add_row([f"Abroad Visit Report - Year {rep['year']}"], style=2, fit=False)
    add_row([f"Generated: {rep['generated']}",
             f"Total employees: {rep['total_employees']}",
             f"Reached yearly limit: {rep['blocked_count']}"], fit=False)
    add_row([""])
    add_row(["YEARLY EMPLOYEE STATISTICS"], style=1, fit=False)
    add_row(SUMMARY_HEADERS, style=1)
    for s in rep["stats"]:
        add_row(_summary_row(s), numeric_cols=(5,))
    add_row([""])
    add_row(["VISIT DETAILS"], style=1, fit=False)
    add_row(DETAIL_HEADERS, style=1)
    for d in rep["details"]:
        if not d["visits"]:
            add_row([d["emp_id"], d["name"], f"(no visits recorded in {year})", "", "", ""])
            continue
        for v in d["visits"]:
            add_row([d["emp_id"], d["name"], v["date"], v["country"], v["title"], v["detail"]])

    cols_xml = "".join(
        f'<col min="{ci}" max="{ci}" width="{min(max(ln * 1.1 + 2, 9), 55):.2f}" customWidth="1"/>'
        for ci, ln in sorted(width_map.items())
    )
    cols_xml = f"<cols>{cols_xml}</cols>" if cols_xml else ""
    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{cols_xml}"
        "<sheetData>" + "".join(sheet_rows) + "</sheetData>"
        "</worksheet>"
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="15"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="Visit Report {rep["year"]}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)


PAGE_W, PAGE_H = 842, 595
_MARGIN = 36
_BOTTOM = 46


def _pdf_escape(text):
    text = str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("cp1252", "replace").decode("cp1252")


def _wrap_pdf(text, max_w, measure, size):
    raw = _ILLEGAL_XML.sub("", str(text)).replace("\n", " ").strip()

    def fits(t):
        return measure(t, size) <= max_w

    out = []
    cur = ""
    for word in raw.split():
        while word and not fits(word):
            piece = word[0]
            i = 1
            while i < len(word) and fits(piece + word[i]):
                piece += word[i]
                i += 1
            if cur:
                out.append(cur)
                cur = ""
            out.append(piece)
            word = word[i:]
        cand = f"{cur} {word}".strip() if cur else word
        if fits(cand):
            cur = cand
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out or [""]


class _TrueTypeFont:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        num_tables = int.from_bytes(d[4:6], "big")
        tables = {}
        for i in range(num_tables):
            off = 12 + 16 * i
            tag = d[off:off + 4].decode("latin-1")
            tables[tag] = int.from_bytes(d[off + 8:off + 12], "big")
        head = tables["head"]
        hhea = tables["hhea"]
        self.units_per_em = int.from_bytes(d[head + 18:head + 20], "big") or 1000
        self.ascent = self._s16(hhea + 4)
        self.descent = self._s16(hhea + 6)
        self.bbox = [round(self._s16(head + o) * 1000 / self.units_per_em) for o in (36, 38, 40, 42)]
        n_metrics = int.from_bytes(d[hhea + 34:hhea + 36], "big")
        hm = tables["hmtx"]
        self.advances = [int.from_bytes(d[hm + 4 * i:hm + 4 * i + 2], "big") for i in range(n_metrics)]
        blob = hb.Blob(self.data)
        self.hb_face = hb.Face(blob)
        self.hb_font = hb.Font(self.hb_face)
        cm = tables["cmap"]
        n_sub = int.from_bytes(d[cm + 2:cm + 4], "big")
        prio = {(3, 10): 5, (0, 4): 5, (0, 6): 5, (3, 1): 3, (0, 1): 3, (0, 2): 3, (0, 3): 3}
        best = None
        for i in range(n_sub):
            base = cm + 4 + 8 * i
            pid = int.from_bytes(d[base:base + 2], "big")
            eid = int.from_bytes(d[base + 2:base + 4], "big")
            soff = int.from_bytes(d[base + 4:base + 8], "big")
            fmt = int.from_bytes(d[cm + soff:cm + soff + 2], "big")
            key = (prio.get((pid, eid), 0), 1 if fmt == 12 else 0)
            if best is None or key > best[0]:
                best = (key, cm + soff, fmt)
        _, sub, fmt = best
        self.cmap = {}
        if fmt == 12:
            ng = int.from_bytes(d[sub + 12:sub + 16], "big")
            for i in range(ng):
                gb = sub + 16 + 12 * i
                start = int.from_bytes(d[gb:gb + 4], "big")
                end = int.from_bytes(d[gb + 4:gb + 8], "big")
                g0 = int.from_bytes(d[gb + 8:gb + 12], "big")
                for c in range(start, min(end, 0x10FFFF) + 1):
                    self.cmap[c] = g0 + (c - start)
        elif fmt == 4:
            seg_x2 = int.from_bytes(d[sub + 6:sub + 8], "big")
            seg = seg_x2 // 2
            ends = sub + 14
            starts = ends + seg_x2 + 2
            deltas = starts + seg_x2
            ranges = deltas + seg_x2
            for i in range(seg):
                end = int.from_bytes(d[ends + 2 * i:ends + 2 * i + 2], "big")
                start = int.from_bytes(d[starts + 2 * i:starts + 2 * i + 2], "big")
                delta = self._s16(deltas + 2 * i)
                ro = int.from_bytes(d[ranges + 2 * i:ranges + 2 * i + 2], "big")
                if start == 0xFFFF:
                    continue
                for c in range(start, end + 1):
                    if ro == 0:
                        g = (c + delta) & 0xFFFF
                    else:
                        addr = ranges + 2 * i + ro + 2 * (c - start)
                        g = int.from_bytes(d[addr:addr + 2], "big")
                        if g:
                            g = (g + delta) & 0xFFFF
                    if g:
                        self.cmap[c] = g

    def _s16(self, off):
        return int.from_bytes(self.data[off:off + 2], "big", signed=True)

    def gid(self, ch):
        return self.cmap.get(ord(ch), 0)

    def width_units(self, gid):
        if not self.advances:
            return self.units_per_em // 2
        return self.advances[gid] if gid < len(self.advances) else self.advances[-1]

    def char_width(self, ch):
        return self.width_units(self.gid(ch))

    def shape_text(self, s):
        buf = hb.Buffer()
        buf.add_str(str(s))
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf)
        infos = buf.glyph_infos or []
        positions = buf.glyph_positions or []
        gids = [info.codepoint for info in infos]
        total_advance = sum(pos.x_advance for pos in positions) if positions else 0
        return gids, total_advance


_FONT_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
]
_FONT_PRIORITY = [
    "kalpurush", "solaimanlipi", "notosansbengali", "nikosh", "nirmala", "vrinda", "shonar", "bangla", "bengali", "sutonny", "charukola",
]


def _font_file(name):
    for d in _FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def _iter_ttf_files():
    seen = set()
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for name in entries:
            if not name.lower().endswith(".ttf"):
                continue
            p = os.path.join(d, name)
            if p not in seen:
                seen.add(p)
                yield p


def _priority_index(path):
    lower = re.sub(r"[^a-z0-9]", "", os.path.basename(path).lower())
    for i, kw in enumerate(_FONT_PRIORITY):
        if kw in lower:
            return i
    return len(_FONT_PRIORITY)


def _bold_variant(path, tf_reg):
    stem, ext = os.path.splitext(path)
    for cand in (stem + "b" + ext, stem + "bd" + ext, stem + "-bold" + ext):
        if os.path.isfile(cand):
            try:
                return _TrueTypeFont(cand)
            except Exception:
                pass
    return tf_reg


def _pick_unicode_fonts(codepoints):
    needed = {cp for cp in codepoints if cp > 126}
    if not needed:
        return None, None
    best = None
    best_tf = None
    for path in sorted(_iter_ttf_files(), key=_priority_index):
        try:
            tf = _TrueTypeFont(path)
        except Exception:
            continue
        coverage = sum(1 for cp in needed if cp in tf.cmap)
        score = (coverage, -_priority_index(path))
        if best is None or score > best:
            best = score
            best_tf = tf
            if coverage == len(needed) and _priority_index(path) < len(_FONT_PRIORITY):
                break
    if best_tf is None:
        return None, None
    return best_tf, _bold_variant(best_tf.path, best_tf)


class _PdfDoc:
    def __init__(self, mode="latin", font_reg=None, font_bold=None):
        self.mode = mode
        self.font_reg = font_reg
        self.font_bold = font_bold
        self.used_gids_reg = set()
        self.used_gids_bold = set()
        self.pages = []
        self._ops = None

    def _new_page(self):
        self._ops = []
        self.pages.append(self._ops)

    def text(self, x_top, y_top, size, s, bold=False, gray=0):
        font = "/F2" if bold else "/F1"
        x = _MARGIN + x_top
        y = PAGE_H - y_top
        raw = _ILLEGAL_XML.sub("", str(s))
        if self.mode == "unicode":
            tf = self.font_bold if bold else self.font_reg
            used = self.used_gids_bold if bold else self.used_gids_reg
            gids, _ = tf.shape_text(raw)
            hexs = []
            for g in gids:
                used.add(g)
                hexs.append(f"{g:04X}")
            payload = "<" + "".join(hexs) + ">"
        else:
            payload = "(" + _pdf_escape(raw) + ")"
        self._ops.append(
            f"{gray} g BT {font} {size} Tf {x:.1f} {y:.1f} Td {payload} Tj ET 0 g"
        )

    def line(self, x_top, y_top, x2_top, y2_top, gray=0.75):
        self._ops.append(
            f"{gray} G 0.6 w {_MARGIN + x_top:.1f} {PAGE_H - y_top:.1f} m {_MARGIN + x2_top:.1f} {PAGE_H - y2_top:.1f} l S 0 G"
        )

    def rect_fill(self, x_top, y_top, w, h, gray=0.9):
        self._ops.append(f"{gray} g {_MARGIN + x_top:.1f} {PAGE_H - y_top - h:.1f} {w:.1f} {h:.1f} re f 0 g")

    def _embed_font(self, add_obj, tf, name, used_gids):
        w_parts = [f"{g} [{round(tf.width_units(g) * 1000 / tf.units_per_em)}]" for g in sorted(used_gids)]
        ascent = round(tf.ascent * 1000 / tf.units_per_em)
        descent = round(tf.descent * 1000 / tf.units_per_em)
        file_id = add_obj(
            f"<< /Length {len(tf.data)} /Length1 {len(tf.data)} >>\nstream\n".encode() + tf.data + b"\nendstream"
        )
        desc_id = add_obj(
            f"<< /Type /FontDescriptor /FontName /{name} /Flags 4 "
            f"/FontBBox [{tf.bbox[0]} {tf.bbox[1]} {tf.bbox[2]} {tf.bbox[3]}] "
            f"/ItalicAngle 0 /Ascent {ascent} /Descent {descent} /CapHeight {ascent} /StemV 80 "
            f"/FontFile2 {file_id} 0 R >>".encode()
        )
        cid_id = add_obj(
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {desc_id} 0 R /DW 1000 /W [{' '.join(w_parts)}] /CIDToGIDMap /Identity >>".encode()
        )
        return add_obj(
            f"<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H "
            f"/DescendantFonts [{cid_id} 0 R] >>".encode()
        )

    def save(self, path):
        objects = []

        def add_obj(body):
            objects.append(body)
            return len(objects)

        catalog_id = add_obj(None)
        pages_id = add_obj(None)
        if self.mode == "unicode":
            f1 = self._embed_font(add_obj, self.font_reg, "ReportFont", self.used_gids_reg)
            f2 = self._embed_font(add_obj, self.font_bold, "ReportFont-Bold", self.used_gids_bold)
        else:
            f1 = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
            f2 = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        page_ids = []
        for ops in self.pages:
            stream = ("\n".join(ops)).encode("cp1252", "replace")
            content_id = add_obj(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
            page_ids.append(add_obj(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> "
                f"/Contents {content_id} 0 R >>".encode()
            ))
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n")
            offsets = []
            for obj_num, body in enumerate(objects, start=1):
                offsets.append(f.tell())
                f.write(f"{obj_num} 0 obj\n".encode() + body + b"\nendobj\n")
            xref_pos = f.tell()
            f.write(f"xref\n0 {len(objects) + 1}\n".encode())
            f.write(b"0000000000 65535 f \n")
            for off in offsets:
                f.write(f"{off:010d} 00000 n \n".encode())
            f.write(
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
            )


class _PdfReport:
    COLS_SUMMARY = [("ID", 55), ("Name", 125), ("Designation", 90), ("Type", 65), ("Project", 100),
                    ("Limit", 42), ("Used", 40), ("Remaining", 68), ("Status", 95), ("Times Max Reached", 90)]
    COLS_DETAIL = [("Date", 70), ("Country", 130), ("Purpose Title", 190), ("Purpose Details", 380)]
    BODY = 8
    HEAD = 8.5
    LEAD = 11

    def __init__(self, rep):
        self.rep = rep
        needed = set()
        for s in rep["stats"]:
            for v in s.values():
                needed.update(ord(c) for c in str(v))
        for d in rep["details"]:
            for v in (d["name"], d["designation"]):
                needed.update(ord(c) for c in str(v))
            for visit in d["visits"]:
                for v in visit.values():
                    needed.update(ord(c) for c in str(v))

        def needs_unicode():
            for cp in needed:
                try:
                    chr(cp).encode("cp1252")
                except UnicodeEncodeError:
                    return True
            return False

        font_reg, font_bold = _pick_unicode_fonts(needed)
        if needs_unicode() and font_reg is not None:
            self.doc = _PdfDoc("unicode", font_reg, font_bold)
        else:
            self.doc = _PdfDoc("latin")
        self.page_no = 0

    def _measure(self, s, size):
        if self.doc.mode == "unicode":
            tf = self.doc.font_reg
            _, total_advance = tf.shape_text(s)
            return total_advance * size / tf.units_per_em
        return len(str(s)) * size * 0.52

    def _start_page(self, first=False):
        self.doc._new_page()
        self.page_no += 1
        if first:
            self.doc.text(0, 34, 15, f"Abroad Visit Report - Year {self.rep['year']}", bold=True)
            meta = (f"Generated: {self.rep['generated']}    Total employees: {self.rep['total_employees']}    "
                    f"Reached yearly limit: {self.rep['blocked_count']}")
            self.doc.text(0, 52, 8.5, meta, gray=0.35)
            self.doc.line(0, 60, PAGE_W - 2 * _MARGIN, 60)
            self.y = 76
        else:
            self.doc.text(0, 26, 8.5, f"Abroad Visit Report - Year {self.rep['year']} (continued)", bold=True)
            self.doc.line(0, 33, PAGE_W - 2 * _MARGIN, 33)
            self.y = 44
        self.doc.text(PAGE_W - 2 * _MARGIN - 60, PAGE_H - 24, 8, f"Page {self.page_no}")

    def _ensure(self, needed):
        if self.y + needed > PAGE_H - _BOTTOM:
            self._start_page(False)

    def _section_title(self, title):
        self._ensure(30)
        self.doc.text(0, self.y + 10, 10.5, title, bold=True)
        self.y += 18

    def _table_header(self, cols):
        h = self.LEAD + 8
        widths = [w for _, w in cols]
        self.doc.rect_fill(0, self.y, sum(widths), h, gray=0.88)
        x = 0
        for (label, w) in cols:
            self.doc.text(x + 4, self.y + 14, self.HEAD, label, bold=True)
            x += w
        self.y += h
        self.doc.line(0, self.y, sum(widths), self.y, gray=0.5)

    def _draw_row(self, cols, cells, bold=False, shade=False):
        size = self.BODY
        wraps = [_wrap_pdf(c, w - 8, self._measure, size) for c, (_, w) in zip(cells, cols)]
        lines = max(len(wl) for wl in wraps)
        rh = lines * self.LEAD + 8
        self._ensure(rh + 6)
        total_w = sum(w for _, w in cols)
        if shade:
            self.doc.rect_fill(0, self.y, total_w, rh, gray=0.93)
        ty = self.y + 4 + size
        x_offsets = []
        acc = 0
        for _, w in cols:
            x_offsets.append(acc)
            acc += w
        for wl, xo, (label, w) in zip(wraps, x_offsets, cols):
            tyy = ty
            for ln in wl:
                self.doc.text(xo + 4, tyy, size, ln, bold=bold)
                tyy += self.LEAD
        self.y += rh
        self.doc.line(0, self.y, total_w, self.y, gray=0.85)

    def write(self):
        self._start_page(first=True)
        self._section_title("YEARLY EMPLOYEE STATISTICS")
        self._table_header(self.COLS_SUMMARY)
        for s in self.rep["stats"]:
            row = [s["emp_id"], s["name"], s["designation"], s["emp_type"], s["project"],
                   s["limit_txt"], s["used"], s["remaining"], s["status"], s["reached"]]
            bold = s["status"].startswith("MAX REACHED")
            self._draw_row(self.COLS_SUMMARY, [str(c) for c in row], bold=bold)
        self.y += 14
        self._section_title("VISIT DETAILS")
        for d in self.rep["details"]:
            group = f'{d["name"]} ({d["emp_id"]}) - {d["designation"]}'
            if d.get("project"):
                group += f' - Project: {d["project"]}'
            if not d["visits"]:
                group += f'  (no visits recorded in {self.rep["year"]})'
            self._ensure(40)
            self.doc.text(0, self.y + 10, 9, group, bold=True)
            self.y += 17
            self._table_header_detail()
            for v in d["visits"]:
                self._draw_row(self.COLS_DETAIL, [v["date"], v["country"], v["title"], v["detail"]])
            self.y += 10

    def _table_header_detail(self):
        self._table_header(self.COLS_DETAIL)

    def save(self, path):
        self.write()
        self.doc.save(path)


def export_pdf(path, year):
    rep = build_report(year)
    _PdfReport(rep).save(path)
