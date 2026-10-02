"""Embed original WMF/EMF in Word without a lossy raster-only conversion."""
import base64
from io import BytesIO
import os

PLACEHOLDER = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def add_vector(paragraph, original, width):
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml.ns import qn
    # DrawingML placement comes from python-docx; the relationship targets the original.
    picture = paragraph.add_run().add_picture(BytesIO(PLACEHOLDER), width=width)
    picture.height = int(width * 0.7)
    package = paragraph.part.package
    name = os.path.basename(original)
    uri = PackURI('/word/media/' + name)
    vector = next((p for p in package.parts if p.partname == uri), None)
    if vector is None:
        content_type = 'image/x-emf' if name.lower().endswith('.emf') else 'image/x-wmf'
        with open(original, 'rb') as stream:
            vector = Part(uri, content_type, stream.read(), package)
    rid = paragraph.part.relate_to(vector, RT.IMAGE)
    picture._inline.find('.//' + qn('a:blip')).set(qn('r:embed'), rid)
    return picture
