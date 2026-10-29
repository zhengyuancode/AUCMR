"""Read-only full-document workspace with bounded original-page tool reads."""
import base64
import re
from functools import lru_cache
from .protocol import file_hash, local_path, require, canonical, digest
from .inputs import alternatives


@lru_cache(maxsize=8)
def _text_pages(path, sha256):
    import fitz
    with fitz.open(path) as pdf:
        return tuple(p.get_text() for p in pdf)


class Workspace:
    def __init__(self, catalog, evaluation, limits):
        self.catalog, self.evaluation, self.limits = catalog, evaluation, limits
        self.task = next(t for t in catalog['targets'] if t['task_id'] == evaluation['task_id'])
        self.sources = {s['source_id']: s for s in catalog['sources'] if s['project_id'] == self.task['project_id']}
        self.pages = {}
        self.images = {}
        self.audit = []
        for sid, source in self.sources.items():
            path = local_path(source['path'])
            sha256 = file_hash(path)
            require(sha256 == source.get('sha256', source.get('expected_sha256')), 'SOURCE_HASH_MISMATCH')
            self.pages[sid] = _text_pages(str(path),sha256)

    def page(self, sid, page, image=False, start=0, length=12000):
        require(sid in self.sources and type(page) is int and 1 <= page <= len(self.pages[sid]), 'INVALID_SOURCE_PAGE')
        require(type(image) is bool and type(start) is int and start >= 0 and type(length) is int and 1 <= length <= 20000, 'INVALID_PAGE_ARGUMENTS')
        text = self.pages[sid][page-1]
        require(start <= len(text), 'INVALID_TEXT_OFFSET')
        value = {'source_id': sid, 'pdf_page': page, 'text': text[start:start+length],
                 'text_start': start, 'text_total_chars': len(text),
                 'text_complete': start == 0 and length >= len(text), 'image_attached': image}
        if image:
            key = (sid, page)
            if key not in self.images:
                import fitz
                with fitz.open(local_path(self.sources[sid]['path'])) as pdf:
                    pix = pdf[page-1].get_pixmap(dpi=144)
                    require(pix.width * pix.height <= 20_000_000, 'PAGE_IMAGE_TOO_LARGE')
                    self.images[key] = base64.b64encode(pix.tobytes('png')).decode()
            value['image_base64'] = self.images[key]
        self.audit.append({k: v for k, v in value.items() if k != 'image_base64'})
        return value

    def initial(self):
        # The complete text is exposed for short documents; long documents get
        # shared title-based initial pages. Every page remains available by tool.
        index, reads = [], []
        terms = set(re.findall(r'[A-Za-z]{3,}', self.task['name'].lower()))
        for sid, pages in self.pages.items():
            index.append({'source_id': sid, 'page_count': len(pages),
                          'pages': [{'pdf_page': i+1, 'opening_text': p.strip()[:120]} for i,p in enumerate(pages)]})
            total = sum(map(len, pages))
            if total <= self.limits['initial_source_text_chars']:
                selected = list(range(len(pages)))
            else:
                scores = [(sum(p.lower().count(t) for t in terms), i) for i,p in enumerate(pages)]
                selected = sorted(i for _,i in sorted(scores, reverse=True)[:3])
            for i in selected:
                # Full initial text for each selected page, split into exact chunks.
                for start in range(0, max(1,len(pages[i])), 20000):
                    reads.append(self.page(sid,i+1,start=start,length=20000))
        task_list = [{k:t[k] for k in ('task_id','name','actor')} for t in self.catalog['targets']
                     if t['project_id'] == self.task['project_id']]
        return {'document_index': index, 'all_document_cases': task_list, 'source_reads': reads,
                'coverage_notice': '原文全文及全部用例结果可通过工具读取；未附图片的页面只读取了文本。未展示内容不等于不存在。'}

    def tools(self, calls):
        require(isinstance(calls, list) and 1 <= len(calls) <= self.limits['tools_per_round'], 'INVALID_TOOL_COUNT')
        results = []
        for call in calls:
            require(isinstance(call, dict) and set(call) == {'name','arguments'} and isinstance(call['arguments'],dict), 'INVALID_TOOL_ENVELOPE')
            name, args = call['name'], call['arguments']
            if name == 'source_page':
                require({'source_id','pdf_page'} <= set(args) <= {'source_id','pdf_page','image','start','length'}, 'INVALID_PAGE_FIELDS')
                result = self.page(args['source_id'],args['pdf_page'],args.get('image',False),args.get('start',0),args.get('length',12000))
            elif name == 'source_search':
                require(set(args) == {'source_id','query'} and args['source_id'] in self.sources and
                        isinstance(args['query'],str) and 1 <= len(args['query']) <= 200, 'INVALID_SEARCH')
                query = args['query'].casefold()
                result = {'matches': [{'pdf_page':i+1,'excerpt':p[max(0,p.casefold().find(query)-150):p.casefold().find(query)+500]}
                                      for i,p in enumerate(self.pages[args['source_id']]) if query in p.casefold()][:8]}
                self.audit.append({'operation':'source_search', 'arguments':args, 'result':result})
            elif name == 'case_outputs':
                require(set(args) == {'task_id'}, 'INVALID_CASE_FIELDS')
                target = next((t for t in self.catalog['targets'] if t['task_id'] == args['task_id']), None)
                require(target is not None and target['project_id'] == self.task['project_id'], 'CASE_OUTSIDE_DOCUMENT')
                e = {**self.evaluation, 'task_id': target['task_id']}
                result = {'task': {k:target[k] for k in ('task_id','name','actor')}, 'alternatives': alternatives(self.catalog,e)}
                self.audit.append({'operation':'case_outputs','task_id':target['task_id'],'result_sha256':digest(result)})
            else:
                raise ValueError('UNKNOWN_TOOL:' + str(name))
            results.append({'operation': name, 'arguments': args, 'data': result})
        return results


def message(data):
    """Move images into standard multimodal parts; preserve provenance in text."""
    images = []
    def visit(value):
        if isinstance(value,dict):
            if 'image_base64' in value:
                images.append({'type':'text','text':canonical({'source_id':value['source_id'],'pdf_page':value['pdf_page']})})
                images.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+value['image_base64']}})
            return {k:visit(v) for k,v in value.items() if k != 'image_base64'}
        if isinstance(value,list):
            return [visit(v) for v in value]
        return value
    clean = visit(data)
    return {'role':'user','content':[{'type':'text','text':canonical(clean)},*images]}
