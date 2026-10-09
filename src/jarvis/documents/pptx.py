"""A real .pptx writer on the standard library.

PresentationML package: presentation part, one slide master, one layout, one
theme, and N slides with a title plus bullet body. No ``python-pptx``.

The package is deliberately minimal but *valid*: PowerPoint requires a slide
master, a layout and a theme to be present and correctly cross-referenced even
when the slides never mention them. Omitting any of those produces a file that
opens with a repair prompt, which is worse than not producing a file at all.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from jarvis.util.clock import now_iso

#: 16:9 at 96dpi, in EMU (English Metric Units: 914400 per inch).
SLIDE_W = 12192000
SLIDE_H = 6858000

_CONTENT_TYPES_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>"""

_CONTENT_TYPES_TAIL = """
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

# A theme is mandatory: the slide master references it by relationship id.
_THEME = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Jarvis">
<a:themeElements>
<a:clrScheme name="Jarvis">
<a:dk1><a:srgbClr val="1A1A1A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="F2F2F2"/></a:lt2>
<a:accent1><a:srgbClr val="2E5496"/></a:accent1><a:accent2><a:srgbClr val="4472C4"/></a:accent2>
<a:accent3><a:srgbClr val="70AD47"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>
<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="ED7D31"/></a:accent6>
<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
</a:clrScheme>
<a:fontScheme name="Jarvis">
<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
</a:fontScheme>
<a:fmtScheme name="Office">
<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>
<a:effectStyle><a:effectLst/></a:effectStyle>
<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
</a:fmtScheme>
</a:themeElements>
</a:theme>"""

_MASTER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
<p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
</p:spTree></p:cSld>
<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
<p:txStyles>
<p:titleStyle><a:lvl1pPr><a:defRPr sz="4000"><a:solidFill><a:schemeClr val="tx2"/></a:solidFill><a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr></p:titleStyle>
<p:bodyStyle><a:lvl1pPr><a:defRPr sz="2000"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl1pPr></p:bodyStyle>
<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle>
</p:txStyles>
</p:sldMaster>"""

_MASTER_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>"""

_LAYOUT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="txAndTitle" preserve="1">
<p:cSld name="Title and Content"><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
</p:spTree></p:cSld>
<p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/></p:clrMapOvr>
</p:sldLayout>"""

_LAYOUT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>"""


def _shape(shape_id: int, name: str, x: int, y: int, cx: int, cy: int, body: str) -> str:
    """One text box. Positions are EMU."""
    return f"""<p:sp>
<p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr><a:lstStyle/>{body}</p:txBody>
</p:sp>"""


def _para(text: str, *, size: int = 2000, bold: bool = False, level: int = 0) -> str:
    """One paragraph. Font size is in hundredths of a point, as OOXML requires."""
    weight = ' b="1"' if bold else ""
    colour = "2E5496" if bold else "1A1A1A"
    return (
        f'<a:p><a:pPr lvl="{level}"/>'
        f'<a:r><a:rPr lang="en-US" sz="{size}"{weight} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill>'
        f'<a:latin typeface="Calibri"/></a:rPr>'
        f"<a:t>{escape(text)}</a:t></a:r></a:p>"
    )


class PptxPresentation:
    """Builds a title-plus-bullets deck."""

    def __init__(self, title: str = "Jarvis presentation") -> None:
        self.title = title
        self.slides: list[dict[str, object]] = []

    def add_slide(
        self,
        title: str,
        bullets: Sequence[str] = (),
        *,
        notes: str = "",
    ) -> PptxPresentation:
        self.slides.append({"title": title, "bullets": list(bullets), "notes": notes})
        return self

    # ------------------------------------------------------------------ parts
    def _slide_xml(self, slide: dict[str, object]) -> str:
        title = str(slide["title"])
        bullets = [str(b) for b in slide["bullets"] if str(b).strip()]
        shapes = [
            _shape(
                2, "Title", 838200, 457200, SLIDE_W - 1676400, 1325563,
                _para(title, size=3600, bold=True),
            )
        ]
        if bullets:
            body = "".join(_para(b, size=2000) for b in bullets)
            shapes.append(
                _shape(3, "Content", 838200, 1900000, SLIDE_W - 1676400, 4200000, body)
            )
        else:
            # An empty body placeholder is what makes a title-only slide look
            # deliberate rather than broken.
            shapes.append(
                _shape(
                    3, "Content", 838200, 2200000, SLIDE_W - 1676400, 1000000,
                    _para("(no detail supplied)", size=1600),
                )
            )
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
{''.join(shapes)}
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""

    def _presentation_xml(self) -> str:
        slide_ids = "".join(
            f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>' for i in range(len(self.slides))
        )
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
<p:sldIdLst>{slide_ids}</p:sldIdLst>
<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/>
<p:notesSz cx="{SLIDE_H}" cy="{SLIDE_W}"/>
</p:presentation>"""

    def _presentation_rels(self) -> str:
        rels = [
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        ]
        for i in range(len(self.slides)):
            rels.append(
                f'<Relationship Id="rId{i + 2}" Type="http://schemas.openxmlformats.org/'
                f'officeDocument/2006/relationships/slide" Target="slides/slide{i + 1}.xml"/>'
            )
        theme_id = len(self.slides) + 2
        rels.append(
            f'<Relationship Id="rId{theme_id}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>"
        )

    def _core_xml(self) -> str:
        stamp = now_iso()
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>{escape(self.title)}</dc:title>
<dc:creator>Jarvis</dc:creator>
<cp:lastModifiedBy>Jarvis</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>
</cp:coreProperties>"""

    def _app_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'extended-properties"><Application>Jarvis</Application>'
            f"<Slides>{len(self.slides)}</Slides></Properties>"
        )

    # ------------------------------------------------------------------ write
    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self.slides:
            self.add_slide(self.title, ["No slides were supplied for this deck."])

        overrides = "".join(
            f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType="application/'
            f'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(len(self.slides))
        )
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES_HEAD + overrides + _CONTENT_TYPES_TAIL)
            zf.writestr("_rels/.rels", _ROOT_RELS)
            zf.writestr("ppt/presentation.xml", self._presentation_xml())
            zf.writestr("ppt/_rels/presentation.xml.rels", self._presentation_rels())
            zf.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
            zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _MASTER_RELS)
            zf.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
            zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _LAYOUT_RELS)
            zf.writestr("ppt/theme/theme1.xml", _THEME)
            for i, slide in enumerate(self.slides):
                zf.writestr(f"ppt/slides/slide{i + 1}.xml", self._slide_xml(slide))
                zf.writestr(
                    f"ppt/slides/_rels/slide{i + 1}.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                    'relationships"><Relationship Id="rId1" Type="http://schemas.'
                    'openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
                    'Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
                )
            zf.writestr("docProps/core.xml", self._core_xml())
            zf.writestr("docProps/app.xml", self._app_xml())
        return target

    # --------------------------------------------------------------- validate
    def validate(self, path: str | Path) -> dict[str, object]:
        """Re-open the package and confirm it is structurally a presentation.

        Checks what makes PowerPoint show a repair prompt: every slide the
        presentation lists must exist, every slide must reference a layout, and
        the master must reference a theme.
        """
        from xml.etree import ElementTree as ET

        problems: list[str] = []
        try:
            archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            return {"ok": False, "problems": [f"not a readable pptx package: {exc}"]}

        with archive as zf:
            names = set(zf.namelist())
            for required in (
                "[Content_Types].xml",
                "ppt/presentation.xml",
                "ppt/slideMasters/slideMaster1.xml",
                "ppt/slideLayouts/slideLayout1.xml",
                "ppt/theme/theme1.xml",
            ):
                if required not in names:
                    problems.append(f"missing part {required}")
            if "ppt/presentation.xml" in names:
                try:
                    root = ET.fromstring(zf.read("ppt/presentation.xml"))
                except ET.ParseError as exc:
                    problems.append(f"presentation.xml does not parse: {exc}")
                    root = None
                if root is not None:
                    listed = [
                        n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                    ]
                    if not listed:
                        problems.append("the deck has no slides")
                    for slide in sorted(listed):
                        try:
                            ET.fromstring(zf.read(slide))
                        except ET.ParseError as exc:
                            problems.append(f"{slide} does not parse: {exc}")
                        rels = f"ppt/slides/_rels/{slide.rsplit('/', 1)[-1]}.rels"
                        if rels not in names:
                            problems.append(f"{slide} has no relationship to a layout ({rels})")
            if "ppt/slideMasters/_rels/slideMaster1.xml.rels" not in names:
                problems.append("the slide master does not reference a theme")

        return {
            "ok": not problems,
            "problems": problems,
            "slides": len(self.slides),
        }


def write_pptx(
    path: str | Path,
    title: str = "Jarvis presentation",
    *,
    slides: Sequence[dict[str, object]] | None = None,
    bullets: Sequence[str] = (),
) -> Path:
    """Convenience entry point matching the other document writers."""
    deck = PptxPresentation(title)
    deck.add_slide(title, list(bullets))
    for slide in slides or ():
        deck.add_slide(
            str(slide.get("title", "")),
            [str(b) for b in slide.get("bullets", ())],
            notes=str(slide.get("notes", "")),
        )
    return deck.write(path)


__all__ = ["PptxPresentation", "SLIDE_H", "SLIDE_W", "write_pptx"]
