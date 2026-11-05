"""Lossless JSON-envelope adaptation. Never repair or invent requirement content."""
import json
import re
import copy
from eval.evaluation_response_format import normalize_json_envelope

FIELDS={'scope','requirements','unresolved_issues','candidate_decisions'}

def expand_malformed_page_keys(text):
    """Recover only the unambiguous evidence key spelling: pdf_page_30,"quote".

    The literal page number is retained. Never run this substitution inside a
    string value, array string, or a correctly formed JSON key/value pair.
    """
    out=[];operations=[];stack=[];quoted=False;escaped=False;last='';i=0
    pattern=re.compile(r'"pdf_page_([1-9][0-9]*)\s*,(?=\s*"(?:quote|visual_region_description)"\s*:)')
    bare_id=re.compile(r'"local_id"\s*:\s*([DR][0-9]+)(?=\s*[,}])')
    while i<len(text):
        char=text[i]
        if not quoted and char=='"' and stack and stack[-1]=='{' and last in {'{',','}:
            identifier=bare_id.match(text,i)
            if identifier:
                out.append('"local_id":'+json.dumps(identifier.group(1)))
                operations.append('QUOTE_BARE_LOCAL_ID:'+identifier.group(1));i=identifier.end();last='"';continue
            match=pattern.match(text,i)
            if match:
                out.append('"pdf_page_1based":'+match.group(1)+',')
                operations.append('EXPAND_MALFORMED_PDF_PAGE_KEY:'+match.group(1))
                i=match.end();last=',';continue
        out.append(char);i+=1
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False;last='"'
            continue
        if char=='"':quoted=True
        elif char in '{[':stack.append(char)
        elif char in '}]':
            if stack:stack.pop()
        if not char.isspace():last=char
    return ''.join(out),operations

def pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('duplicate JSON key: '+key)
        result[key]=value
    return result

def eof_closers(text):
    """Close only structurally open containers, never strings, values or commas."""
    stack=[];quoted=False;escaped=False
    for char in text:
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char=='"':quoted=True
        elif char in '{[':stack.append(char)
        elif char in '}]':
            if not stack or stack.pop()!=('{' if char=='}' else '['):return ''
    if quoted:return ''
    return ''.join('}' if char=='{' else ']' for char in reversed(stack))

def decode(body):
    text,_=normalize_json_envelope(body)
    operations=[]
    if isinstance(text,str):
        text,operations=expand_malformed_page_keys(text)
        kwargs={'object_pairs_hook':pairs,'parse_constant':lambda x:(_ for _ in ()).throw(ValueError('nonfinite JSON'))}
        try:data=json.loads(text,**kwargs)
        except json.JSONDecodeError as error:
            # One observed response closed the root immediately after scope,
            # then continued its disjoint draft fields. Remove only that exact
            # redundant root brace; never change strings, values or duplicate keys.
            decoder=json.JSONDecoder(**kwargs)
            try:first,end=decoder.raw_decode(text)
            except json.JSONDecodeError:first,end=None,0
            if (isinstance(first,dict) and set(first)=={'scope'}
                    and isinstance(first['scope'],dict)
                    and 'unresolved_issues' in first['scope']
                    and re.match(r'\s*,\s*"requirements"\s*:',text[end:])):
                data=json.loads(text[:end-1]+text[end:],**kwargs)
                operations.append('REMOVE_PREMATURE_ROOT_CLOSE_AFTER_SCOPE')
            elif error.msg=='Extra data':
                decoder=json.JSONDecoder(**kwargs);objects=[];offset=0
                while offset<len(text):
                    while offset<len(text) and text[offset].isspace():offset+=1
                    if offset==len(text):break
                    item,offset=decoder.raw_decode(text,offset);objects.append(item)
                if len(objects)!=2 or not all(isinstance(x,dict) for x in objects):raise ValueError('expected exactly two disjoint JSON objects')
                if set(objects[0])&set(objects[1]):raise ValueError('conflicting concatenated JSON objects')
                data={**objects[0],**objects[1]};operations.append('JOIN_DISJOINT_JSON_OBJECTS')
            else:
                suffix=eof_closers(text)
                if error.pos<len(text.rstrip()) or not suffix:raise
                data=json.loads(text+suffix,**kwargs)
                operations.append('CLOSE_EOF_CONTAINERS:'+suffix)
    else:data=text
    if not isinstance(data,dict):raise ValueError('expected JSON object')
    data=copy.deepcopy(data)
    tools={'tool_calls':data['tool_calls']} if 'tool_calls' in data else {}
    fields=set(data)-{'tool_calls'}
    nested=data.get('scope')
    if fields=={'scope'} and isinstance(nested,dict) and {'requirements','unresolved_issues'}<=set(nested):
        data={**tools,'draft':{'scope':nested}};operations.append('WRAP_SCOPE_WITH_NESTED_DRAFT_FIELDS')
    elif {'scope','requirements','unresolved_issues'}<=fields<=FIELDS:
        data={**tools,'draft':{k:v for k,v in data.items() if k!='tool_calls'}};operations.append('WRAP_BARE_DRAFT')
    elif ({'scope','requirements'}<=fields<=FIELDS and isinstance(nested,dict)
          and 'unresolved_issues' in nested):
        data={**tools,'draft':{k:v for k,v in data.items() if k!='tool_calls'}};operations.append('WRAP_DRAFT_WITH_NESTED_ISSUES')
    elif 'draft' in data and fields-{'draft'} and fields-{'draft'}<=FIELDS:
        draft=data['draft']
        if not isinstance(draft,dict) or set(draft)&(fields-{'draft'}):
            raise ValueError('conflicting split draft fields')
        data={**tools,'draft':{**draft,**{k:v for k,v in data.items() if k not in {'draft','tool_calls'}}}}
        operations.append('MOVE_DISJOINT_DRAFT_FIELDS_INSIDE_ENVELOPE')
    if set(data) not in ({'draft'},{'tool_calls'},{'draft','tool_calls'}):
        raise ValueError('expected draft or tool_calls envelope')
    if 'draft' in data and isinstance(data['draft'],dict):
        draft=data['draft'];scope=draft.get('scope')
        if isinstance(scope,dict):
            lifted=(FIELDS-{'scope'})&set(scope)
            if lifted:
                if lifted&set(draft):raise ValueError('conflicting nested draft fields')
                data={**tools,'draft':{**draft,**{k:scope[k] for k in lifted},'scope':{k:v for k,v in scope.items() if k not in lifted}}}
                operations.append('LIFT_DISJOINT_FIELDS_FROM_SCOPE:'+','.join(sorted(lifted)))
        if isinstance(data['draft'].get('requirements'),list):
            for row in data['draft']['requirements']:
                if isinstance(row,dict) and isinstance(row.get('conditions'),(dict,list)):
                    original=row['conditions'];encoded=json.dumps(original,ensure_ascii=False,separators=(',',':'),allow_nan=False)
                    if json.loads(encoded)!=original:raise ValueError('conditions serialization was not lossless')
                    row['conditions']=encoded
                    operations.append('ENCODE_STRUCTURED_CONDITIONS_JSON:'+str(row.get('local_id','')))
        groups=[data['draft'].get('scope',{})]
        for key in ('requirements','candidate_decisions'):
            if isinstance(data['draft'].get(key),list):groups+=data['draft'][key]
        for group in groups:
            if not isinstance(group,dict) or not isinstance(group.get('evidence'),list):continue
            for e in group['evidence']:
                if not isinstance(e,dict):continue
                aliases=[k for k in e if re.fullmatch(r'pdf_page_[0-9]+based',k) and k!='pdf_page_1based']
                if not aliases:continue
                if len(aliases)!=1 or 'pdf_page_1based' in e:raise ValueError('conflicting page coordinate fields')
                alias=aliases[0]
                if type(e[alias]) is not int:raise ValueError('nonstandard page field must contain an integer')
                e['pdf_page_1based']=e.pop(alias)
                operations.append('NORMALIZE_PDF_PAGE_FIELD_NAME:'+alias)
    return data,operations


def tools_already_delivered(tools, messages, entries=()):
    """Only reuse uncropped pages actually present in earlier model messages.

    A local tool request in a mixed response does not prove the model received
    its answer. All unfulfilled tools must be returned on the next model turn.
    """
    import base64
    import hashlib
    for tool in tools:
        if not isinstance(tool,dict) or set(tool)!={'name','arguments'}:
            raise ValueError('invalid mixed tool request')
        args=tool['arguments']
        if tool['name']!='source_page' or not isinstance(args,dict):return False
        if type(args.get('pdf_page')) is not int or not isinstance(args.get('source_id'),str):return False
        if set(args)-{'source_id','pdf_page','image','dpi'}:return False
        if args.get('image') and args.get('dpi',144)!=144:return False
        matched=False
        for message in messages:
            if message.get('role')!='user':continue
            content=message.get('content')
            if isinstance(content,str):
                try:body=json.loads(content)
                except (TypeError,ValueError):continue
                if not isinstance(body,dict) or body.get('source_id')!=args.get('source_id'):continue
                if not args.get('image') and any(p.get('pdf_page_1based')==args.get('pdf_page') for p in body.get('pdf_pages',[])):
                    matched=True
                if not args.get('image') and body.get('pdf_page')==args.get('pdf_page') and 'text' in body:matched=True
            elif isinstance(content,list) and args.get('image'):
                metadata=[];images=set()
                for part in content:
                    if part.get('type')=='text':
                        try:metadata.append(json.loads(part['text']))
                        except (TypeError,ValueError):pass
                    elif part.get('type')=='image_url':
                        url=part.get('image_url',{}).get('url','')
                        if url.startswith('data:image/png;base64,'):
                            images.add(hashlib.sha256(base64.b64decode(url.split(',',1)[1],validate=True)).hexdigest())
                for m in metadata:
                    if not isinstance(m,dict) or m.get('source_id')!=args.get('source_id') or m.get('pdf_page')!=args.get('pdf_page'):continue
                    if any(e.get('access_id')==m.get('access_id') and e.get('image_sha256') in images and e.get('dpi')==144 and not e.get('bbox') for e in entries):matched=True
        if not matched:return False
    return True
