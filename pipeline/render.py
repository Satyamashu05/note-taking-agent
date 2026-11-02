from fpdf import FPDF
from fpdf.enums import XPos, YPos
from pptx import Presentation
from pptx.util import Inches

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

def section_for(t, sections):
    idx = 0
    for i, s in enumerate(sections):
        if s["start_sec"] <= t:
            idx = i
    return idx

def render_pdf(notes, frames, info, out_path):
    pdf = FPDF()
    pdf.add_font("DejaVu", fname=FONT)
    pdf.add_page()

    def para(text, size, h):
        pdf.set_font("DejaVu", size=size)
        pdf.multi_cell(0, h, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    para(notes["title"], 18, 10)
    para(f"https://youtu.be/{info['id']}", 9, 6)
    para(notes["overview"], 11, 6)
    for i, sec in enumerate(notes["sections"]):
        pdf.ln(4)
        para(f"{sec['heading']}  ({sec['start_sec'] // 60}:{sec['start_sec'] % 60:02d})", 14, 8)
        for b in sec["bullets"]:
            para(f"• {b}", 11, 6)
        for f in frames:
            if section_for(f["timestamp_sec"], notes["sections"]) == i:
                pdf.image(str(f["image"]), w=170)
                para(f"[{f['timestamp_sec']}s] {f['caption']} - {f['reason']}", 9, 5)
    pdf.output(str(out_path))

def render_pptx(notes, frames, out_path):
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = notes["title"]
    s.placeholders[1].text = notes["overview"]
    for sec in notes["sections"]:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = sec["heading"]
        s.placeholders[1].text = "\n".join(sec["bullets"])
    for f in frames:
        s = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        s.shapes.title.text = f["caption"]
        s.shapes.add_picture(str(f["image"]), Inches(0.75), Inches(1.3), height=Inches(4.8))
        tb = s.shapes.add_textbox(Inches(0.75), Inches(6.3), Inches(8.5), Inches(0.8))
        tb.text_frame.text = f"{f['timestamp_sec']}s - {f['reason']}"
    prs.save(str(out_path))
