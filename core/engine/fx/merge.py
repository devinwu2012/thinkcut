"""Compare V1/A1 by source positions, including copies with new item IDs."""
from .ledger import digest


def structure(timeline):
    rows = [{k: i.get(k) for k in ('type', 'track', 'start', 'end', 'media_path', 'media_type',
                                  'media_fps', 'source_start', 'source_end', 'enabled')}
            for i in timeline['items'] if i['type'] in ('video', 'audio') and i['track'] == 1]
    return {'start': timeline['start'], 'fps': timeline['fps'],
            'items': sorted(rows, key=lambda i: (i['type'], i['start'], i['end']))}


def fingerprint(timeline):
    return digest(structure(timeline))


def linear(item):
    s, e = item.get('source_start'), item.get('source_end')
    return s is not None and e is not None and abs(e-s+1-(item['end']-item['start'])) <= 1.1
