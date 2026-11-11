"""Changes between persisted session snapshots; strings and lists keep their prefix."""
from copy import deepcopy


def snapshot_changes(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() - after.keys():
            yield [list((*path, key)), 'delete']
        for key, value in after.items():
            location = (*path, key)
            if key not in before:
                yield [list(location), 'set', value]
            else:
                yield from snapshot_changes(before[key], value, location)
    elif isinstance(before, list) and isinstance(after, list):
        common = 0
        while common < min(len(before), len(after)) and before[common] == after[common]:
            common += 1
        if common != len(before) or common != len(after):
            yield [list(path), 'tail', common, after[common:]]
    elif before != after:
        if isinstance(before, str) and isinstance(after, str) and before and after.startswith(before):
            yield [list(path), 'append', after[len(before):]]
        else:
            yield [list(path), 'set', after]


def apply_changes(snapshot, changes):
    for path, operation, *values in changes:
        parent = snapshot
        for key in path[:-1]:
            parent = parent[key]
        key = path[-1]
        if operation == 'delete':
            del parent[key]
        elif operation == 'tail':
            parent[key][values[0]:] = deepcopy(values[1])
        elif operation == 'append':
            parent[key] += values[0]
        elif operation == 'set':
            parent[key] = deepcopy(values[0])
        else:
            raise ValueError(f'Unknown session change: {operation}')
