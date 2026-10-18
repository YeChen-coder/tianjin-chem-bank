"""Read-only inventory of Word sources (no Office automation)."""
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile, is_zipfile
from lxml import etree as E

def inventory(root):
    result = []
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.doc', '.docx'}:
            continue
        item = {'path': str(path), 'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'kind': 'legacy_doc'}
        try:
            if is_zipfile(path):
                with ZipFile(path) as z:
                    xml = E.fromstring(z.read('word/document.xml'), E.XMLParser(resolve_entities=False, no_network=True))
                    names = Counter(E.QName(n).localname for n in xml.iter() if isinstance(n.tag, str))
                    item.update(kind='docx', paragraphs=names['p'], tables=names['tbl'],
                                omml=names['oMath'], objects=names['OLEObject'],
                                images=names['blip'] + names['imagedata'])
        except Exception as exc:
            item.update(kind='invalid_package', error=str(exc))
        result.append(item)
    return {'files': result, 'summary': dict(Counter(x['kind'] for x in result)),
            'duplicate_hash_groups': [v for v in _group(result).values() if len(v) > 1]}

def _group(items):
    result = {}
    for item in items:
        result.setdefault(item['sha256'], []).append(item['path'])
    return result

if __name__ == '__main__':
    import sys
    data = inventory(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(data['summary']))
    for item in data['files']:
        if item['sha256'] == 'e4fe1b55db17b1d7ba694e2fd0cbbac18d8c2acc08f93521530df1c765c3cc8c':
            print(json.dumps(item, ensure_ascii=False))
