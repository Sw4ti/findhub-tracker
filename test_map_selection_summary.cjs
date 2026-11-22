const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, 'ui', 'mapSelectionSummary.ts'), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const loaded = { exports: {} }
new Function('exports', 'require', 'module', compiled)(loaded.exports, require, loaded)
const { mapSelectionSummary } = loaded.exports

const devices = [
  { device: 'synthetic-device-1', label: 'Demo Tracker Alpha' },
  { device: 'synthetic-device-2', label: 'Demo Tracker Beta' },
]

test('all trackers with no date filters does not invent a time range', () => {
  assert.deepEqual(mapSelectionSummary('', devices, 14), {
    tracker: 'All trackers', timeScope: 'All dates', count: 14,
  })
})

test('single tracker and selected bounds use Sydney local time', () => {
  const summary = mapSelectionSummary(
    'device=synthetic-device-1&start=2026-01-01T00%3A00%3A00Z&end=2026-01-02T02%3A30%3A00Z',
    devices,
    3,
  )
  assert.equal(summary.tracker, 'Demo Tracker Alpha')
  assert.equal(summary.count, 3)
  assert.match(summary.timeScope, /1 Jan 2026, 11:00 am \u2013 2 Jan 2026, 1:30 pm \(Sydney time\)/)
})

test('one-sided ranges show only the active boundary', () => {
  assert.match(mapSelectionSummary('start=2026-06-30T14%3A00%3A00Z', devices, 1).timeScope,
    /^From 1 July 2026, 12:00 am \(Sydney time\)$/)
  assert.match(mapSelectionSummary('end=2026-06-30T13%3A00%3A00Z', devices, 1).timeScope,
    /^Through 30 June 2026, 11:00 pm \(Sydney time\)$/)
})

test('the summary count is the supplied visible map point count, including zero', () => {
  const empty = mapSelectionSummary('device=synthetic-device-2&start=2026-01-01T00%3A00%3A00Z', devices, 0)
  assert.equal(empty.tracker, 'Demo Tracker Beta')
  assert.equal(empty.count, 0)
})

test('unknown identifiers do not leak IDs and malformed bounds remain understandable', () => {
  const summary = mapSelectionSummary('device=private-id&start=not-a-date&end=also-invalid', devices, 2)
  assert.equal(summary.tracker, 'Selected tracker')
  assert.equal(summary.timeScope, 'Selected date/time range')
  assert.equal(JSON.stringify(summary).includes('private-id'), false)
})
