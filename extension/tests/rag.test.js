const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validateRagResponse, callRagDirect, retrievalContext, renderRetrieval } = require('../dist/rag');

const data = () => ({ query_id: 'test-id', feedback_available: true, chunks: [
  { citation: '[1]', title: 'First', content: 'First context', url: 'https://example.com', score: .4 },
  { citation: '[2]', title: 'Second', content: 'Second context', url: 'https://example.com', score: .3 },
  { citation: '[3]', title: 'Local content', content: 'Local context', url: '', score: .2 },
], metrics: { latency_ms: 10, chunks_retrieved: 3 } });

test('validates responses and rejects malformed responses rather than treating them as empty', () => {
  assert.equal(validateRagResponse(data()).chunks.length, 3);
  assert.deepEqual(validateRagResponse({ query_id: 'id', chunks: [] }).chunks, []);
  for (const value of [null, {}, { chunks: [] }, { ...data(), chunks: {} }, { ...data(), chunks: [{ title: 'missing content' }] }, { ...data(), metrics: { latency_ms: 'fast' } }]) {
    assert.throws(() => validateRagResponse(value), /Malformed/);
  }
});

test('keeps citation labels and appends sources/details independently of generated answer', async () => {
  assert.match(retrievalContext(data()), /\[1\] First/);
  const output = await renderRetrieval(data(), true, async () => 'Answer [2]', () => false);
  assert.match(output, /Answer \[2\]/);
  for (const label of ['[1] First', '[2] Second', '[3] Local content', 'Query ID: test-id']) {
    assert.ok(output.includes(label));
  }
});

test('source toggle, unavailable feedback, generation failure and empty results', async () => {
  const value = data(); value.feedback_available = false;
  const output = await renderRetrieval(value, false, async () => { throw Error('model failed'); }, () => false);
  assert.match(output, /First context/);
  assert.match(output, /feedback unavailable/);
  assert.doesNotMatch(output, /SOURCES|https:\/\//);
  const empty = await renderRetrieval({ query_id: 'empty-id', chunks: [] }, true, async () => assert.fail('must not generate'), () => false);
  assert.match(empty, /not available in the internal knowledge base/);
});

test('cancellation suppresses fallback and generated output', async () => {
  let cancelled = false;
  assert.equal(await renderRetrieval(data(), true, async () => { cancelled = true; throw Error('cancelled'); }, () => cancelled), '');
  assert.equal(await renderRetrieval(data(), true, async () => assert.fail(), () => true), '');
});

test('direct HTTP request contract, failure and AbortSignal propagation', async () => {
  const original = global.fetch;
  try {
    const controller = new AbortController();
    global.fetch = async (url, options) => {
      assert.equal(url, 'http://test/query');
      assert.deepEqual(JSON.parse(options.body), { query: 'question', retrieve_only: true });
      assert.equal(options.signal, controller.signal);
      return new Response(JSON.stringify(data()), { status: 200 });
    };
    assert.equal((await callRagDirect('http://test/query', 'question', controller.signal)).chunks.length, 3);
    global.fetch = async () => new Response('unavailable', { status: 503 });
    await assert.rejects(callRagDirect('http://test/query', 'question', controller.signal), /503.*unavailable/);
    global.fetch = async (_url, options) => { options.signal.throwIfAborted(); };
    controller.abort();
    await assert.rejects(callRagDirect('http://test/query', 'question', controller.signal), { name: 'AbortError' });
  } finally { global.fetch = original; }
});

// Shared with the Python API producer test; keep consumer compatibility explicit.
test('accepts the shared Lassy retrieval response contract', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '../../contracts/retrieval-response.json'), 'utf8'));
  const response = validateRagResponse(fixture);
  assert.equal(response.query_id, fixture.query_id);
  assert.deepEqual(response.chunks, fixture.chunks);
});
