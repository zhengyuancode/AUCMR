"""Deterministic source locator extraction; never rewrite requirement claims.

Exact quotes may be relocated/split across adjacent supplied pages. Explicit
section IDs may be re-anchored to the source section, with the original quote
retained in a receipt-bound patch. This locates evidence, not semantic support.
"""
import copy
import re
import unicodedata
from .validation import normalized, quote_in_page, check_evidence


def mapped(text, dash=False):
    """Return normalized text and original character offsets."""
    omitted={m.start() for m in re.finditer(r'(?<=\w)-\s*\n(?=\w)',text)}
    out=[];positions=[]
    translate=str.maketrans({'‘':"'",'’':"'",'“':'"','”':'"'})
    for index,char in enumerate(text):
        if index in omitted:continue
        if char=='\xad':char='-' if dash else ''
        value=unicodedata.normalize('NFKC',char).replace('\u200b','').translate(translate).casefold()
        for c in value:
            if not c.isspace():out.append(c);positions.append(index)
    return ''.join(out),positions


class LocatorIndex:
    def __init__(self, context, entries):
        source=context['source'];self.context=context;self.entries=entries
        self.pages={p:t for p,t in context['pages'].items() if any(e.get('operation')=='page' and e.get('source_id')==source['source_id'] and e.get('source_sha256')==source['pdf_sha256'] and e.get('pdf_page')==p for e in entries)}
        self.variants={p:[mapped(t),mapped(t,True)] for p,t in self.pages.items()}
        chunks=[];self.offsets=[];offset=0
        for page,text in sorted(self.pages.items()):
            chunks.append(text+'\n');self.offsets.append((page,offset,offset+len(text)));offset+=len(text)+1
        self.text=''.join(chunks)
        headings=list(re.finditer(r'(?m)^[ \t]*(\d+(?:\.\d+){2,})[ \t]*([^\n]*)',self.text))
        self.sections={}
        for index,h in enumerate(headings):
            end=headings[index+1].start() if index+1<len(headings) else len(self.text)
            self.sections.setdefault(h.group(1),[]).append((h.start(),end,h.group(2).strip()))

    def span(self,start,end):
        result=[]
        for page,left,right in self.offsets:
            if start<right and end>left:
                quote=self.text[max(start,left):min(end,right)].strip()
                if quote:result.append({'pdf_page_1based':page,'quote':quote,'visual_region_description':''})
        return result

    def exact(self,quote):
        needle=normalized(quote)
        if not needle:return None
        hits={}
        for p,variants in self.variants.items():
            for haystack,positions in variants:
                start=haystack.find(needle)
                while start>=0:
                    key=(p,positions[start],positions[start+len(needle)-1]+1)
                    hits[key]=[{'pdf_page_1based':p,'quote':self.pages[p][key[1]:key[2]],'visual_region_description':''}]
                    start=haystack.find(needle,start+1)
        for p in self.pages:
            if p+1 not in self.pages:continue
            for left,lmap in self.variants[p]:
                for right,rmap in self.variants[p+1]:
                    start=(left+right).find(needle)
                    while start>=0:
                        end=start+len(needle)
                        if start<len(left)<end:
                            key=(p,lmap[start],p+1,rmap[end-len(left)-1]+1)
                            hits[key]=[{'pdf_page_1based':p,'quote':self.pages[p][lmap[start]:].strip(),'visual_region_description':''},
                                       {'pdf_page_1based':p+1,'quote':self.pages[p+1][:key[-1]].strip(),'visual_region_description':''}]
                        start=(left+right).find(needle,start+1)
        return next(iter(hits.values())) if len(hits)==1 else None

    def section(self,quote):
        anchors=[]
        for match in re.finditer(r'(?<![\w.])(\d+(?:\.\d+){2,})(?![\d.])',quote):
            matches=self.sections.get(match.group(1),[])
            if len(matches)!=1:continue
            start,end,title=matches[0]
            if not title or not normalized(quote[match.end():]).startswith(normalized(title)):continue
            if (start,end) not in anchors:anchors.append((start,end))
        if not anchors:return None
        rows=[]
        for start,end in anchors:rows+=self.span(start,end)
        return rows

    def locate(self,e):
        try:
            check_evidence([e],self.context,self.entries)
            return [copy.deepcopy(e)],None
        except (ValueError,KeyError,TypeError):pass
        if not isinstance(e,dict) or type(e.get('pdf_page_1based')) is not int or not 1<=e['pdf_page_1based']<=self.context['source']['pages']:
            return None,None
        quote=e.get('quote')
        if not isinstance(quote,str) or not quote.strip():return None,None
        rows=self.exact(quote);reason='UNIQUE_EXACT_SOURCE_TEXT_RELOCATION'
        if rows is None:
            rows=self.section(quote);reason='EXPLICIT_SOURCE_SECTION_REANCHOR_LOCATOR_ONLY_NOT_SEMANTIC_VALIDATION'
        if rows is None:return None,None
        try:check_evidence(rows,self.context,self.entries)
        except (ValueError,KeyError,TypeError):return None,None
        return rows,reason


def propose(draft, context, entries):
    """Propose evidence-array edits only; unresolved locators stay unresolved."""
    index=LocatorIndex(context,entries);repaired=copy.deepcopy(draft);patches=[]
    def change(node,path,after,reason):
        before=copy.deepcopy(node.get('evidence'))
        if before==after:return
        patches.append({'path':path,'before_present':'evidence' in node,'before':before,'after':after,'reason':reason})
        node['evidence']=copy.deepcopy(after)
    groups=[(repaired.get('scope',{}),['scope','evidence'])]
    for key in ('requirements','candidate_decisions'):
        if isinstance(repaired.get(key),list):
            groups += [(row,[key,n,'evidence']) for n,row in enumerate(repaired[key]) if isinstance(row,dict)]
    for node,path in groups:
        evidence=node.get('evidence')
        if not isinstance(evidence,list) or not evidence:continue
        after=[];reasons=[]
        for original in evidence:
            rows,reason=index.locate(original)
            if rows is None:
                after.append(copy.deepcopy(original));continue
            after+=rows
            if reason:reasons.append(reason)
        if reasons:change(node,path,after,'; '.join(sorted(set(reasons))))
    scope=repaired.get('scope')
    if isinstance(scope,dict) and not scope.get('evidence'):
        rows=[];seen=set()
        for row in repaired.get('requirements',[]):
            try:check_evidence(row.get('evidence'),context,entries)
            except (ValueError,KeyError,TypeError):continue
            for evidence in row['evidence']:
                key=(evidence.get('pdf_page_1based'),evidence.get('quote'),evidence.get('visual_region_description',''))
                if key not in seen:seen.add(key);rows.append(copy.deepcopy(evidence))
        if rows:change(scope,['scope','evidence'],rows,'DERIVED_SCOPE_SOURCE_INDEX_FROM_SAME_AGENT_REQUIREMENT_LOCATORS; NOT_AN_AGENT_SCOPE_JUSTIFICATION_OR_SEMANTIC_VALIDATION')
    return repaired,patches
