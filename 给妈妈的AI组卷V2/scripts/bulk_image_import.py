"""Local Windows OCR selects image-only exam questions; images stay authoritative."""
import hashlib,io,json,posixpath,re,shutil,subprocess,sys
from pathlib import Path
from zipfile import ZipFile,is_zipfile
from lxml import etree
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from question_content.http_api import ContentAPI
from question_content.import_selection import filename_reason,inspect_package,CHEM,ASK
from question_content.validation import validate_bundle
from question_content.bank_metadata import question_text
WORK=ROOT/'.work/session-4/bulk-image';WORK.mkdir(parents=True,exist_ok=True)
SOURCE=ROOT.parent/'chem-papers/mirror'
HEADER=re.compile(r'^(\d{1,2})(?:[.．](?!\d)|[、:：)）]|(?=下列|某|在|现|用|将|已知|实验|为了|如图|[（(]\d{4}))')
STRONG=re.compile(r'下列|请|回答|写出|求出|计算.{0,40}[？?]|[_＿]{2,}|(?:正确|错误|不正确|不合理).{0,8}(?:是|有)|质量比为|质量分数是')


def compact(s):return re.sub(r'\s+','',s)
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    api=ContentAPI(ROOT/'.work/session-4/content-v2',ROOT);flow=api.workflow;receipts=[]
    for path in sorted(SOURCE.rglob('*')):
        if path.suffix.lower() not in {'.doc','.docx'} or filename_reason(path) or not is_zipfile(path):continue
        check=inspect_package(path)
        if check['eligible'] or check.get('error') or not CHEM.search(path.stem):continue
        sha=hashlib.sha256(path.read_bytes()).hexdigest();directory=WORK/sha;directory.mkdir(exist_ok=True)
        row={'path':path.relative_to(SOURCE).as_posix(),'source_sha256':sha,'questions':0,'status':'no_verified_questions'}
        try:
            original=directory/('original'+path.suffix.lower())
            if not original.exists():shutil.copyfile(path,original)
            questions=[];assets={};manifest={};notes=[]
            with ZipFile(path) as z:
                r=etree.fromstring(z.read('word/document.xml'))
                rels={e.get('Id'):posixpath.normpath('word/'+e.get('Target')) for e in etree.fromstring(z.read('word/_rels/document.xml.rels')) if e.get('TargetMode')!='External'}
                parts=[]
                for e in r.iter():
                    if not isinstance(e.tag,str):continue
                    if etree.QName(e).localname in {'blip','imagedata'}:
                        rid=e.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed') or e.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                        if rid in rels and rels[rid] not in parts:parts.append(rels[rid])
                for seq,part in enumerate(parts):
                    try:image=Image.open(io.BytesIO(z.read(part))).convert('RGB')
                    except Exception:continue
                    if image.width<350 or image.height<180:continue
                    png=directory/f'image-{seq}.png';image.save(png)
                    resized=image.copy();resized.thumbnail((2400,2400));ocrpng=directory/f'ocr-{seq}.png';resized.save(ocrpng)
                    output=directory/f'ocr-{seq}.json'
                    if not output.exists():
                        command=['C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-File',str(ROOT/'scripts/ocr_image.ps1'),'-InputPath',str(ocrpng),'-OutputPath',str(output)]
                        p=subprocess.run(command,capture_output=True,timeout=45)
                        if p.returncode:raise RuntimeError('Windows OCR failed: '+p.stderr.decode('utf-8',errors='replace')[:200])
                    ocr=json.loads(output.read_text(encoding='utf-8'));lines=[]
                    for line in ocr['lines']:
                        if not line['words']:continue
                        lines.append({'text':compact(line['text']),'x':min(w['x'] for w in line['words']),'y':min(w['y'] for w in line['words'])})
                    starts=[]
                    for line in lines:
                        m=HEADER.match(line['text'])
                        if m:starts.append({**line,'number':int(m[1])})
                    split=ocr['width']/2
                    columns=[(0,ocr['width'],starts)]
                    left=[s for s in starts if s['x']<split];right=[s for s in starts if s['x']>=split]
                    if len(left)>=2 and len(right)>=2:columns=[(0,split,left),(split,ocr['width'],right)]
                    for x0,x1,marks in columns:
                        column_lines=[l for l in lines if x0<=l['x']<x1];marks.sort(key=lambda s:s['y'])
                        if marks:
                            prefix=''.join(l['text'] for l in column_lines if l['y']<marks[0]['y'])
                            if STRONG.search(prefix):marks.insert(0,{'y':0,'number':max(1,marks[0]['number']-1)})
                        else:
                            text=''.join(l['text'] for l in column_lines)
                            if STRONG.search(text) and len(re.findall('下列',text))<=1:marks=[{'y':0,'number':len(questions)+1}]
                        for index,mark in enumerate(marks):
                            y0=max(0,mark['y']-4);y1=marks[index+1]['y']-4 if index+1<len(marks) else ocr['height']
                            text='\n'.join(l['text'] for l in column_lines if y0<=l['y']<y1)
                            if not STRONG.search(compact(text)):notes.append({'image':part,'number':mark['number'],'reason':'OCR 分段没有可靠设问，未导入'});continue
                            if y1-y0<30:continue
                            sx=image.width/ocr['width'];sy=image.height/ocr['height'];box=(int(x0*sx),int(y0*sy),int(x1*sx),min(image.height,int(y1*sy)))
                            qid='image-q-'+hashlib.sha256((sha+part+str(box)).encode()).hexdigest()[:24]
                            target=directory/(qid+'.png');image.crop(box).save(target);asset=api.store.register_asset(target);assets[asset['id']]=asset
                            manifest[asset['id']]={'asset':asset,'local_path':str(target),'preview_path':None}
                            nid=qid+'-image';issue={'id':qid+'-review','code':'IMAGE_QUESTION_REVIEW','severity':'review','question_id':qid,'node_id':nid,'message':'图像试题保留原图；分题边界与内容需要核对，图片内公式尚未转为可编辑公式。'}
                            q={'id':qid,'revision':0,'number':str(mark['number']),'qtype':'未知','body':[{'id':nid,'type':'image','asset_id':asset['id'],'width_mm':158,'height_mm':round(158*(box[3]-box[1])/(box[2]-box[0]),2)}],'options':[],'subquestions':[],'answers':[],'explanations':[],'source':{'source_sha256':sha,'number':str(mark['number']),'locator':part+':crop:'+str(box),'ocr_search_text':text,'ocr_engine':ocr['engine'],'ocr_status':'review_required'},'origin':'import','issues':[issue]}
                            if qid not in {q['id'] for q in questions}:questions.append(q)
            if questions:
                bundle={'schema_version':1,'paper':{'id':'paper-'+sha[:24],'title':path.stem,'source_sha256':sha,'question_ids':[q['id'] for q in questions]},'questions':questions,'assets':list(assets.values()),'issues':[i for q in questions for i in q['issues']]}
                validation=validate_bundle(bundle)
                if not validation['ok']:raise ValueError(json.dumps(validation['errors'],ensure_ascii=False))
                write(directory/'asset-manifest.json',manifest)
                report={'status':'processed_review_required','question_count':len(questions),'formula_count':0,'image_count':len(questions),'original_copy_path':str(original),'asset_manifest_path':str(directory/'asset-manifest.json'),'source':{'sha256':sha,'path':str(path),'unchanged':True},'importer_version':'local-image-questions-v1'}
                result={'bundle':bundle,'report':report};write(directory/'import-result.json',result)
                imported=flow.add_import(result,path.stem,create_saved_paper=False)
                for q in api.store.snapshot(imported['paper_id'])['questions']:
                    lid=flow._legacy_id(q['id'])
                    with flow.legacy() as con:con.execute('INSERT OR IGNORE INTO sources(question_id,rel_path,orig_qnum) VALUES (?,?,?)',(lid,row['path'],q['number']))
                row.update(status='image_imported',questions=len(questions),paper_id=imported['paper_id'])
            row['notes']=notes;row['source_unchanged']=hashlib.sha256(path.read_bytes()).hexdigest()==sha
        except Exception as e:row.update(status='error',reason=str(e))
        receipts.append(row);write(WORK/'results.json',receipts);print(json.dumps(row,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
