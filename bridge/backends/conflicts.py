"""Merge conflicts: find the <<<<<<< / ======= / >>>>>>> blocks and resolve them one by one.

Supports the diff3 style too (||||||| base). A block is resolved with "ours",
"theirs" or "both" (ours, then theirs); when a file has no markers left it is
staged with git add. Line endings of the file are kept.
"""
import re

START, BASE, MID, END = re.compile(r'^<{7}( .*)?$'), re.compile(r'^\|{7}( .*)?$'), re.compile(r'^={7}$'), re.compile(r'^>{7}( .*)?$')


def parse(text):
    """[{'index', 'start', 'end', 'ours', 'theirs', 'base', 'ours_label', 'theirs_label'}], line numbers 1-based."""
    lines = text.split('\n')
    blocks, i = [], 0
    while i < len(lines):
        if START.match(lines[i].rstrip('\r')):
            block = {'index': len(blocks), 'start': i + 1, 'ours': [], 'theirs': [], 'base': [],
                     'ours_label': lines[i].rstrip('\r')[8:], 'theirs_label': ''}
            part = 'ours'
            j = i + 1
            while j < len(lines):
                line = lines[j].rstrip('\r')
                if BASE.match(line) and part == 'ours':
                    part = 'base'
                elif MID.match(line) and part in ('ours', 'base'):
                    part = 'theirs'
                elif END.match(line) and part == 'theirs':
                    block['theirs_label'] = line[8:]
                    block['end'] = j + 1
                    break
                else:
                    block[part].append(lines[j])
                j += 1
            if 'end' in block:
                blocks.append(block)
                i = j
        i += 1
    return blocks


def resolve(text, index, choice):
    if choice not in ('ours', 'theirs', 'both'):
        raise ValueError('Выбор: ours | theirs | both')
    blocks = parse(text)
    if not 0 <= index < len(blocks):
        raise ValueError('Нет такого конфликта (файл изменился?) — обнови список: /cc git conflicts')
    b = blocks[index]
    lines = text.split('\n')
    keep = b['ours'] if choice == 'ours' else b['theirs'] if choice == 'theirs' else b['ours'] + b['theirs']
    return '\n'.join(lines[:b['start'] - 1] + keep + lines[b['end']:])
