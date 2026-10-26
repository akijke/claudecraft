"""Mimics `claude auth login|status|logout` output for tests (state in FAKE_AUTH_FILE)."""
import json
import os
import sys

state = os.environ['FAKE_AUTH_FILE']
command = sys.argv[1:3]
if command == ['auth', 'status']:
    logged = os.path.exists(state)
    print(json.dumps({'loggedIn': logged, 'authMethod': 'claude.ai' if logged else 'none',
                      'email': 'player@example.com' if logged else None}))
elif command == ['auth', 'logout']:
    if os.path.exists(state):
        os.remove(state)
    print('Successfully logged out')
elif command == ['auth', 'login']:
    print('Opening browser to sign in…')
    print('If the browser didn\'t open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc')
    print('Paste code here if prompted > ', end='', flush=True)
    code = sys.stdin.readline().strip()
    if code == 'good-code#abc':
        open(state, 'w').close()
        print('Login successful.')
    else:
        print('OAuth error: invalid code')
        sys.exit(1)
