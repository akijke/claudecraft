"""A tiny language server for tests (stdio, real LSP framing).

- every didOpen/didChange: one diagnostic per line containing "error" (severity 1)
- definition: the first line that starts with "def <word under the cursor>"
- completion: the words of the document starting with the prefix before the cursor
"""
import json
import re
import sys

docs = {}


def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b'content-length:'):
            length = int(line.split(b':', 1)[1])
    return json.loads(sys.stdin.buffer.read(length))


def send(message):
    body = json.dumps(message).encode('utf-8')
    sys.stdout.buffer.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
    sys.stdout.buffer.flush()


def publish(uri):
    diags = [{'range': {'start': {'line': i, 'character': l.find('error')}, 'end': {'line': i, 'character': l.find('error') + 5}},
              'severity': 1, 'message': f'found error on line {i + 1}', 'source': 'fake'}
             for i, l in enumerate(docs[uri].split('\n')) if 'error' in l]
    send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics', 'params': {'uri': uri, 'diagnostics': diags}})


def word_at(text, line, col):
    l = text.split('\n')[line]
    start = col
    while start > 0 and (l[start - 1].isalnum() or l[start - 1] == '_'):
        start -= 1
    end = col
    while end < len(l) and (l[end].isalnum() or l[end] == '_'):
        end += 1
    return l[start:col], l[start:end]


while True:
    m = read()
    method, params = m.get('method'), m.get('params') or {}
    if method == 'initialize':
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'capabilities': {'textDocumentSync': 1}}})
    elif method == 'textDocument/didOpen':
        docs[params['textDocument']['uri']] = params['textDocument']['text']
        publish(params['textDocument']['uri'])
    elif method == 'textDocument/didChange':
        docs[params['textDocument']['uri']] = params['contentChanges'][-1]['text']
        publish(params['textDocument']['uri'])
    elif method == 'textDocument/definition':
        uri, pos = params['textDocument']['uri'], params['position']
        _, word = word_at(docs[uri], pos['line'], pos['character'])
        for i, l in enumerate(docs[uri].split('\n')):
            if re.match(rf'def {re.escape(word)}\b', l):
                send({'jsonrpc': '2.0', 'id': m['id'], 'result': [{'uri': uri, 'range': {'start': {'line': i, 'character': 4},
                                                                                          'end': {'line': i, 'character': 4}}}]})
                break
        else:
            send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
    elif method == 'textDocument/completion':
        uri, pos = params['textDocument']['uri'], params['position']
        prefix, _ = word_at(docs[uri], pos['line'], pos['character'])
        words = sorted({w for w in re.findall(r'[A-Za-z_]\w+', docs[uri]) if w.startswith(prefix) and w != prefix})
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': {'isIncomplete': False, 'items': [{'label': w} for w in words]}})
    elif method == 'shutdown':
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
    elif method == 'exit':
        sys.exit(0)
    elif 'id' in m:
        send({'jsonrpc': '2.0', 'id': m['id'], 'result': None})
