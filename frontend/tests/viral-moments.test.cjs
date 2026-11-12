// Fork (Clipper merge): one import per viral moment, downloading only that range.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTs } = require('./load-ts.cjs')

class FormDataMock { constructor() { this.entries = [] } append(k, v) { this.entries.push([k, String(v)]) } get(k) { const e = this.entries.find(x => x[0] === k); return e ? e[1] : null } getAll(k) { return this.entries.filter(x => x[0] === k).map(x => x[1]) } }

const mod = loadTs(path.join(__dirname, '../src/features/studio/ViralMomentsPanel.tsx'), {
  react: { useEffect() {}, useRef: v => ({ current: v }), useState: v => [v, () => {}] },
  'react-i18next': { useTranslation: () => ({}) }, '../../i18n': { t: x => x }, '../../ui': { Btn: 'button' },
  './api': { studioApi: {}, errorText: String }, 'react/jsx-runtime': { jsx: () => null, jsxs: () => null, Fragment: 'f' },
  __globals: { FormData: FormDataMock },
})

test('clock formats stream offsets', () => {
  assert.equal(mod.clock(65), '1:05')
  assert.equal(mod.clock(3725), '1:02:05')
})

test('each moment imports only its own range with the current preset', () => {
  const result = { url: 'https://www.youtube.com/watch?v=O3I0BcLkFGc', title: 'Stream', moments: [] }
  const options = { goal: 'auto', language: 'source', aspect: 'portrait', duration: 60, instruction: 'clutch', portrait_style: 'streamer' }
  const body = mod.momentForm(result, { start: 4394, end: 4451 }, options, ['tiktok', 'youtube_shorts'], 'chrome')
  assert.equal(body.get('clip_start'), '4394')
  assert.equal(body.get('clip_end'), '4451')
  assert.equal(body.get('goal'), 'highlight')
  assert.equal(body.get('portrait_style'), 'streamer')
  assert.equal(body.get('auto_start'), 'true')
  assert.equal(body.get('browser'), 'chrome')
  assert.deepEqual(body.getAll('platforms'), ['tiktok', 'youtube_shorts'])
  assert.equal(body.get('name'), 'Stream · 1:13:14')
})
