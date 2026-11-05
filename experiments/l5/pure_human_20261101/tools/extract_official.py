from pathlib import Path
import hashlib,json,zipfile,subprocess,datetime,re
ROOT=Path(__file__).resolve().parents[1]
NAMES=['0000 - inventory','2001 - hats','2003 - pnnl','2003 - agentmom','2005 - triangle','2008 - vub','0000 - gamma j','2007 - central trading system','2008 - keepass','2008 - peering']

def main():
    archive=ROOT/'evidence/requirements.zip';data=archive.read_bytes()
    assert hashlib.md5(data).hexdigest()=='bc319fe28619f6290badff328ca159dd'
    z=zipfile.ZipFile(archive);meta=[]
    for name in NAMES:
        member='req/'+name+'.pdf';body=z.read(member)
        dest=ROOT/'evidence/pure'/Path(member).name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
        subprocess.run(['pdftotext','-layout',str(dest),str(dest.with_suffix('.txt'))],check=True,capture_output=True)
        text=dest.with_suffix('.txt').read_text();pages=text.split('\f');pages=pages[:-1] if not pages[-1].strip() else pages
        summary=[]
        for i,page in enumerate(pages,1):
            summary.append({'page':i,'words':len(page.split()),'use_case_mentions':len(re.findall(r'use[ -]?case',page,re.I)),
                'headings':[line.strip() for line in page.splitlines() if re.match(r'^\s*(?:\d+(?:\.\d+)*[.)]?\s+[A-Z]|Use Case|Use case|UC[ -]?\d|Actor|Precondition|Postcondition|Basic [Ff]low|Main [Ff]low)',line)][:50]})
        meta.append({'document_id':name,'member':member,'source_path':str(dest.relative_to(ROOT)),
            'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'pages':len(pages),'extracted_words':len(text.split()),'page_index':summary})
    lock={'source':'https://zenodo.org/records/7118517','download_url':'https://zenodo.org/records/7118517/files/requirements.zip?download=1',
          'version':'2.0','acquired_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'official_md5':'bc319fe28619f6290badff328ca159dd','archive_sha256':hashlib.sha256(data).hexdigest(),
          'archive_bytes':len(data),'archive_members':z.namelist(),'documents':meta}
    (ROOT/'evidence/source_manifest.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n')
    for m in meta:print(m['document_id'],m['pages'],'pages',m['extracted_words'],'words','UC pages',[(p['page'],p['use_case_mentions']) for p in m['page_index'] if p['use_case_mentions']])

if __name__=='__main__':main()
