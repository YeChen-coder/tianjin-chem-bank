"""Repair a ZIP container checksum only when the affected PNG verifies intact."""
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile,BadZipFile,ZIP_DEFLATED
from PIL import Image


def repair_png_crc(source,target):
    source=Path(source);target=Path(target);before=hashlib.sha256(source.read_bytes()).hexdigest()
    entries=[];repaired=[]
    with ZipFile(source) as archive:
        for info in archive.infolist():
            try:data=archive.read(info)
            except BadZipFile as error:
                if 'Bad CRC-32' not in str(error) or not info.filename.lower().endswith('.png'):raise
                # The DEFLATE stream remains unchanged. PNG's own chunk checks
                # independently verify that the media payload is still valid.
                with archive.open(info) as member:
                    member._expected_crc=None
                    data=member.read()
                with Image.open(io.BytesIO(data)) as image:
                    if image.format!='PNG':raise ValueError('Damaged member is not a PNG')
                    image.verify()
                repaired.append({'part':info.filename,'sha256':hashlib.sha256(data).hexdigest()})
            entries.append((info.filename,data))
    if not repaired:raise ValueError('No verifiable PNG checksum fault')
    target.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(target,'w',compression=ZIP_DEFLATED) as archive:
        for name,data in entries:archive.writestr(name,data)
    with ZipFile(target) as archive:
        if archive.testzip() is not None:raise ValueError('Rebuilt package does not verify')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=before:raise ValueError('Original package changed')
    return {'source_sha256':before,'repaired_members':repaired,'media_pixels_changed':False}
