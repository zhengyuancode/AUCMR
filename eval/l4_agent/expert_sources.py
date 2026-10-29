"""One frozen, method-independent PDF context per task; no model retrieval gate."""
from .protocol import L4Error, digest, file_hash, read_json, save_new
from .inputs import local_path
from .sources import SourceTools


def prepare(config, task, sources, image_pages, destination):
    """Read ALL PDF text pages and explicit task-level original images, offline."""
    sources = [s for s in sources if s['project_id'] == task['project_id']]
    tools = SourceTools(sources, task['project_id'], config['source_policy'],
                        destination.with_suffix('.reads.jsonl'), task['task_id'])
    for source in sources:
        for page in range(1, source['pages'] + 1):
            result = tools.invoke('source_page', {'source_id': source['source_id'], 'pdf_page': page, 'image': False})
            if result['status'] != 'OK':
                raise L4Error('SOURCE_CONTEXT_PREPARATION_FAILED:' + result['status'])
        for page in image_pages.get(source['source_id'], []):
            result = tools.invoke('source_page', {'source_id': source['source_id'], 'pdf_page': page, 'image': True, 'dpi': 144})
            if result['status'] != 'OK':
                raise L4Error('SOURCE_IMAGE_PREPARATION_FAILED:' + result['status'])
    pack = {'task_id': task['task_id'], 'project_id': task['project_id'],
            'source_hashes': {s['source_id']: s['sha256'] for s in sources},
            'coverage': 'FULL_PDF_EXTRACTED_TEXT_PLUS_DECLARED_ORIGINAL_PAGE_IMAGES',
            'image_pages': image_pages, 'accesses': tools.entries}
    pack['sha256'] = digest(pack)
    save_new(destination, pack)
    return {'path': destination.relative_to(local_path('.')).as_posix(), 'sha256': file_hash(destination)}


def load(config, task, sources):
    binding = config['source_contexts'][task['task_id']]
    path = local_path(binding['path'])
    if file_hash(path) != binding['sha256']:
        raise L4Error('SOURCE_CONTEXT_CHANGED')
    pack = read_json(path)
    expected = {s['source_id']: s['sha256'] for s in sources if s['project_id'] == task['project_id']}
    if (pack['sha256'] != digest({k: v for k, v in pack.items() if k != 'sha256'}) or
        pack['task_id'] != task['task_id'] or pack['project_id'] != task['project_id'] or pack['source_hashes'] != expected):
        raise L4Error('SOURCE_CONTEXT_IDENTITY_MISMATCH')
    wanted = {(s['source_id'], p) for s in sources if s['project_id'] == task['project_id'] for p in range(1, s['pages'] + 1)}
    observed = []
    images = []
    for i, access in enumerate(pack['accesses']):
        args = access['arguments']
        if (access['access_id'] != 'L4-ACCESS-' + str(i + 1) or access['operation'] != 'source_page' or
            access['result']['status'] != 'OK' or access['source_sha256'] != expected.get(args['source_id'])):
            raise L4Error('INVALID_SOURCE_CONTEXT_ACCESS')
        pair = (args['source_id'], args['pdf_page'])
        if pair not in wanted:
            raise L4Error('SOURCE_CONTEXT_PAGE_OUT_OF_RANGE')
        if args.get('image'):
            if not access['result'].get('image_base64'):
                raise L4Error('SOURCE_CONTEXT_IMAGE_MISSING')
            images.append(pair)
        else:
            if not isinstance(access['result'].get('text'), str):
                raise L4Error('SOURCE_CONTEXT_TEXT_MISSING')
            observed.append(pair)
    expected_images = [(sid, p) for sid, pages in pack['image_pages'].items() for p in pages]
    if set(observed) != wanted or len(observed) != len(wanted) or sorted(images) != sorted(expected_images):
        raise L4Error('SOURCE_CONTEXT_COVERAGE_MISMATCH')
    return pack
