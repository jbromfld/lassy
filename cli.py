"""Command-line client for the standalone retrieval service."""
import argparse
import json
import os

import requests

BASE_URL = os.getenv('RAG_SERVICE_URL', 'http://localhost:8000').rstrip('/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('health', help='Check service readiness')
    commands.add_parser('metrics', help='View retrieval and feedback statistics')
    ingest = commands.add_parser('ingest', help='Ingest a single URL')
    ingest.add_argument('url')
    recursive = commands.add_parser('ringest', help='Recursively ingest a URL')
    recursive.add_argument('url')
    recursive.add_argument('depth', nargs='?', type=int, default=3)
    recursive.add_argument('max_pages', nargs='?', type=int, default=50)
    query = commands.add_parser('query', help='Retrieve relevant chunks')
    query.add_argument('text')
    query.add_argument('--top-k', type=int, help='Override service retrieval count')
    feedback = commands.add_parser('feedback', help='Submit a score from 0 to 10')
    feedback.add_argument('query_id')
    feedback.add_argument('score', type=int, choices=range(11))
    feedback.add_argument('comment', nargs='?', default='')
    args = parser.parse_args()

    try:
        if args.command in ('health', 'metrics'):
            response = requests.get(f'{BASE_URL}/{args.command}', timeout=30)
        else:
            endpoint = args.command
            if endpoint in ('ingest', 'ringest'):
                payload = {'url': args.url}
                if endpoint == 'ringest':
                    payload.update(depth=args.depth, max_pages=args.max_pages)
                endpoint = 'ingest'
            elif endpoint == 'query':
                payload = {'query': args.text, 'retrieve_only': True}
                if args.top_k is not None:
                    payload['top_k'] = args.top_k
            else:
                payload = {'query_id': args.query_id, 'score': args.score, 'comment': args.comment}
            response = requests.post(f'{BASE_URL}/{endpoint}', json=payload, timeout=300)
        try:
            print(json.dumps(response.json(), indent=2))
        except ValueError:
            print(response.text)
        print(f'\nStatus: {response.status_code}')
        return 0 if response.ok else 1
    except requests.RequestException as exc:
        print(f'ERROR: Could not complete request to {BASE_URL}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
