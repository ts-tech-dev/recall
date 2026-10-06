"""Word documents: headings, lists, tables and images in reading order."""

from __future__ import annotations

import re
from pathlib import Path

from .base import Context, ExtractedDoc, cache_image, image_size_ok, md_table, to_browser_image

def extract_docx(path: Path, ctx: Context) -> ExtractedDoc:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    parts: list[str] = []
    img_count = 0

    def images_in(el) -> list[str]:
        nonlocal img_count
        out = []
        for blip in el.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed"))
            part = d.part.related_parts.get(rid) if rid else None
            if part is None or not hasattr(part, "blob"):
                continue
            data = part.blob
            conv = to_browser_image(data, Path(str(part.partname)).suffix)
            if not conv or not image_size_ok(conv[0]):
                continue
            img_count += 1
            out.append(f"![Image {img_count}]({cache_image(ctx, path, rid + str(img_count), *conv)})")
        return out

    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            p = Paragraph(el, d)
            text = p.text.strip()
            style = (p.style.name if p.style is not None else "") or ""
            imgs = images_in(el)
            if text:
                m = re.match(r"Heading (\d)", style)
                if m:
                    parts.append("#" * min(int(m.group(1)), 6) + " " + text)
                elif style == "Title":
                    parts.append("# " + text)
                elif "List Number" in style:
                    parts.append("1. " + text)
                elif "List" in style or el.find(".//" + qn("w:numPr")) is not None:
                    parts.append("- " + text)
                else:
                    parts.append(text)
            parts.extend(imgs)
        elif el.tag == qn("w:tbl"):
            t = Table(el, d)
            rows = [[c.text for c in r.cells] for r in t.rows]
            parts.append(md_table(rows))
            parts.extend(images_in(el))
    title = d.core_properties.title or path.stem
    return ExtractedDoc(title=title, markdown="\n\n".join(p for p in parts if p))
