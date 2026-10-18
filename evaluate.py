"""Evaluate the deployed shared retrieval pipeline against document-level judgments."""
import argparse
import json
from pathlib import Path

import requests


def score(chunks, expected):
    # Rank distinct documents in the order their first chunk appears.
    ranked = list(dict.fromkeys((c.get('collection'), c.get('document_id')) for c in chunks))
    relevant = {(e['collection'], str(e['document_id'])) for e in expected}
    if not relevant:
        raise ValueError('Each evaluation case needs at least one expected document')
    hits = relevant.intersection(ranked)
    reciprocal_rank = next((1 / rank for rank, doc in enumerate(ranked, 1) if doc in relevant), 0.)
    return {'recall': len(hits) / len(relevant), 'reciprocal_rank': reciprocal_rank,
            'retrieved': [{'collection': c, 'document_id': d} for c, d in ranked]}


def evaluate(cases, url, top_k):
    results = []
    with requests.Session() as session:
        for case in cases:
            response = session.post(url.rstrip('/') + '/query', json={
                'query': case['query'], 'filters': case.get('filters'), 'top_k': top_k}, timeout=120)
            response.raise_for_status()
            data = response.json()
            results.append({'query': case['query'], 'query_id': data.get('query_id'),
                            **score(data['chunks'], case['expected'])})
    if not results:
        raise ValueError('Dataset must contain at least one evaluation case')
    return {'top_k_chunks': top_k, 'cases': len(results),
            'mean_document_recall': sum(r['recall'] for r in results) / len(results),
            'mean_reciprocal_document_rank': sum(r['reciprocal_rank'] for r in results) / len(results),
            'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--url', default='http://127.0.0.1:8082')
    parser.add_argument('--top-k', type=int, default=10)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error('--top-k must be positive')
    cases = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    print(json.dumps(evaluate(cases, args.url, args.top_k), indent=2))


if __name__ == '__main__':
    main()
