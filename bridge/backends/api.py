import os
from aiohttp import ClientSession, ClientTimeout


async def run(bridge, chat, text):
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise ValueError('Set ANTHROPIC_API_KEY in bridge environment for API chats')
    messages = chat.messages[-40:] + [{'role': 'user', 'content': text}]
    async with ClientSession(timeout=ClientTimeout(total=300)) as session:
        async with session.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
            json={'model': bridge.config['api_model'], 'max_tokens': 4096,
                  'system': 'Answer in the user language. Plain text, concise.',
                  'messages': messages}) as response:
            data = await response.json()
            if response.status != 200:
                raise ValueError(f'Anthropic API HTTP {response.status}: '
                                 + str(data.get('error', {}).get('message', 'Request failed'))[:500])
    answer = '\n'.join(b['text'] for b in data['content'] if b['type'] == 'text')
    if not answer:
        raise ValueError('API returned no text')
    chat.messages = (messages + [{'role': 'assistant', 'content': answer}])[-40:]
    bridge.emit(chat, answer, kind='text')

