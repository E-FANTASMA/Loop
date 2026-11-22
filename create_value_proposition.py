from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

OUTPUT = "Loop_Venture_Value_Proposition.docx"

doc = Document()
section = doc.sections[0]
section.top_margin = Inches(0.65)
section.bottom_margin = Inches(0.65)
section.left_margin = Inches(0.75)
section.right_margin = Inches(0.75)

styles = doc.styles
styles["Normal"].font.name = "Aptos"
styles["Normal"].font.size = Pt(10)
styles["Title"].font.name = "Georgia"
styles["Title"].font.size = Pt(34)
styles["Heading 1"].font.name = "Georgia"
styles["Heading 1"].font.size = Pt(22)
styles["Heading 1"].font.color.rgb = RGBColor(23, 73, 54)
styles["Heading 2"].font.name = "Aptos"
styles["Heading 2"].font.size = Pt(13)

def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)

def set_margins(cell, top=120, start=140, bottom=120, end=140):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn("w:" + margin))
        if node is None:
            node = OxmlElement("w:" + margin)
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")

def add_label(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(7)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(102, 116, 83)

def add_bullets(items):
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.add_run(item)

# Cover
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
r = p.add_run("VENTURE BRIEF  /  2026")
r.bold = True
r.font.size = Pt(9)
r.font.color.rgb = RGBColor(102, 116, 83)

doc.add_paragraph()
title = doc.add_paragraph(style="Title")
title.add_run("LOOP")

sub = doc.add_paragraph()
sub.paragraph_format.space_after = Pt(14)
r = sub.add_run("Give good things another go.")
r.bold = True
r.font.size = Pt(19)
r.font.color.rgb = RGBColor(23, 73, 54)

tag = doc.add_table(rows=1, cols=2)
tag.autofit = False
tag.columns[0].width = Inches(4.5)
tag.columns[1].width = Inches(2.1)
tag.cell(0, 0).text = "A trusted, campus-first reuse marketplace"
tag.cell(0, 1).text = "PROTOTYPE\nREADY"
shade(tag.cell(0, 0), "EAF0DD")
shade(tag.cell(0, 1), "174936")
for cell in tag.row_cells(0):
    set_margins(cell)
tag.cell(0, 1).paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
for run in tag.cell(0, 1).paragraphs[0].runs:
    run.bold = True
    run.font.color.rgb = RGBColor(255, 255, 255)

doc.add_paragraph()
add_label("THE OPPORTUNITY")
doc.add_heading("Useful items become waste because campus exchange is inconvenient.", level=1)
p = doc.add_paragraph(
    "At term boundaries and throughout the year, students replace furniture, textbooks, "
    "kitchenware and devices. General marketplaces are fragmented and often feel unsafe or "
    "too distant, while campus noticeboards are hard to search and items quickly become obsolete."
)
p.paragraph_format.space_after = Pt(12)

add_label("THE IDEA")
table = doc.add_table(rows=1, cols=3)
table.style = "Table Grid"
cards = [
    ("1  FIND", "Search by item, category and nearby campus location."),
    ("2  CONNECT", "Use a verified student profile and in-app chat."),
    ("3  REUSE", "Meet in a trusted pickup spot and complete the handoff.")
]
for i, (head, text) in enumerate(cards):
    cell = table.cell(0, i)
    shade(cell, "F5F3E9")
    set_margins(cell)
    p = cell.paragraphs[0]
    r = p.add_run(head + "\n")
    r.bold = True
    r.font.color.rgb = RGBColor(23, 73, 54)
    p.add_run(text)

doc.add_page_break()

doc.add_heading("Value proposition", level=1)
add_label("ONE-SENTENCE POSITIONING")
quote = doc.add_table(rows=1, cols=1)
shade(quote.cell(0, 0), "174936")
set_margins(quote.cell(0, 0), top=220, start=220, bottom=220, end=220)
p = quote.cell(0, 0).paragraphs[0]
r = p.add_run(
    "Loop is a verified campus marketplace that helps students quickly find, "
    "give away or affordably trade useful items without the risk and friction of broad social platforms."
)
r.bold = True
r.font.size = Pt(16)
r.font.color.rgb = RGBColor(255, 255, 255)

add_label("PRIMARY CUSTOMER")
doc.add_heading("Students who are moving, setting up or need supplies.", level=2)
add_bullets([
    "Incoming and departing students with a deadline.",
    "Cost-conscious students buying everyday essentials.",
    "Students with usable items that deserve a second owner."
])

add_label("CUSTOMER PAINS")
table = doc.add_table(rows=3, cols=2)
pain_rows = [
    ("Pain", "Loop's relief"),
    ("Hard to locate nearby items", "Campus-area search and location filters"),
    ("Safety and stranger concerns", "Institution-verified profiles and trusted pickup points")
]
for row_i, row in enumerate(pain_rows):
    for col_i, text in enumerate(row):
        cell = table.cell(row_i, col_i)
        cell.text = text
        set_margins(cell)
        if row_i == 0:
            shade(cell, "DCE8A5")
            for run in cell.paragraphs[0].runs:
                run.bold = True

doc.add_heading("Value created", level=1)
table = doc.add_table(rows=1, cols=3)
for i, (head, text) in enumerate([
    ("SAVE", "Lower costs and fewer duplicate purchases"),
    ("CONVENIENCE", "A searchable, deadline-aware campus feed"),
    ("COMMUNITY", "Visible reuse impact and stronger student networks")
]):
    cell = table.cell(0, i)
    shade(cell, "F5F3E9")
    set_margins(cell)
    p = cell.paragraphs[0]
    r = p.add_run(head + "\n")
    r.bold = True
    r.font.color.rgb = RGBColor(23, 73, 54)
    p.add_run(text)

doc.add_page_break()

doc.add_heading("Business model", level=1)
add_label("START SMALL")
table = doc.add_table(rows=4, cols=3)
rows = [
    ("Revenue stream", "Timing", "Initial form"),
    ("Promoted listings", "Post–launch", "Optional student society or society-hosted boosts"),
    ("Campus partnerships", "Pilot", "Student-union or accommodation sponsorship"),
    ("Future commissions", "Scale", "Small fee for high-value electronics or furniture")
]
for r_i, row in enumerate(rows):
    for c_i, text in enumerate(row):
        cell = table.cell(r_i, c_i)
        cell.text = text
        set_margins(cell)
        if r_i == 0:
            shade(cell, "DCE8A5")
            for run in cell.paragraphs[0].runs:
                run.bold = True

add_label("GO-TO-MARKET")
doc.add_heading("Launch where urgent, visible need already exists.", level=2)
add_bullets([
    "Partner with one accommodation hall or student union.",
    "Run a move-out week campaign with a simple, free-to-list flow.",
    "Place QR codes and ambassadors at bins, student kitchens and recycling points.",
    "Use item-success stories and community impact counters to build trust."
])

add_label("DISTRIBUTION")
p = doc.add_paragraph(
    "Primary launch channel: student union and accommodation newsletters. "
    "Organic growth channel: WhatsApp, residence group chats and campus social accounts."
)

doc.add_heading("Pilot and success metrics", level=1)
table = doc.add_table(rows=5, cols=3)
metrics = [
    ("Hypothesis", "Measure", "Pilot target"),
    ("Students will list useful items", "Active listings / 100 students", "25%"),
    ("Listings will lead to exchanges", "Successful handoffs / listing", "≥ 35%"),
    ("Users feel safer than on broad platforms", "Post-pickup trust score", "≥ 4.5/5"),
    ("The loop creates value repeatedly", "90-day repeat-use rate", "≥ 20%")
]
for r_i, row in enumerate(metrics):
    for c_i, text in enumerate(row):
        cell = table.cell(r_i, c_i)
        cell.text = text
        set_margins(cell)
        if r_i == 0:
            shade(cell, "174936")
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)

doc.add_page_break()

doc.add_heading("Prototype", level=1)
add_label("WHAT TO TEST")
doc.add_heading("Can a student find a relevant item and complete a listing in under two minutes?", level=2)

table = doc.add_table(rows=1, cols=2)
table.style = "Table Grid"
left = table.cell(0, 0)
right = table.cell(0, 1)
shade(left, "F5F3E9")
shade(right, "EAF0DD")
set_margins(left)
set_margins(right)
left.text = "Core discovery journey\n\n• Open search\n• Filter by category\n• Search an item\n• Review location and price"
right.text = "Core supply journey\n\n• Select Post a listing\n• Enter item details\n• Choose category and price\n• Publish"

add_label("EXPERIMENT PLAN")
for text in [
    "Recruit 12–15 students across incoming, current and departing groups.",
    "Ask participants to find a desk lamp and post a spare textbook.",
    "Observe completion time, hesitation points and abandoned steps.",
    "Follow up on whether they would use Loop for a real transaction."
]:
    p = doc.add_paragraph(style="List Number")
    p.add_run(text)

doc.add_heading("Prototype links", level=1)
p = doc.add_paragraph()
r = p.add_run("Interactive MVP: ")
r.bold = True
p.add_run("index.html")
p = doc.add_paragraph()
r = p.add_run("Open in a browser to test search, category filters, sorting and listing publication.")
r.italic = True
r.font.color.rgb = RGBColor(102, 116, 83)

doc.add_heading("Key venture risks", level=1)
risks = [
    ("Supply–demand imbalance", "Pilot in one hall and seed listings before launch."),
    ("Low trust", "Require institutional verification and supervised pickup areas."),
    ("Item quality disputes", "Use condition labels and a simple confirmation step."),
    ("One-off seasonality", "Expand from move-out to year-round academic and household needs.")
]
table = doc.add_table(rows=1, cols=2)
table.style = "Table Grid"
for i, (risk, response) in enumerate(risks):
    if i == 0:
        left = table.cell(0, 0)
        right = table.cell(0, 1)
    else:
        row = table.add_row()
        left = row.cells[0]
        right = row.cells[1]
    left.text = risk
    right.text = response
    left.paragraphs[0].runs[0].bold = True
    for cell in (left, right):
        set_margins(cell)

doc.save(OUTPUT)
print(OUTPUT)
