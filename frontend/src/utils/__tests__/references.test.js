import { test } from 'node:test'
import assert from 'node:assert/strict'

import { asReferenceList } from '../references.js'

test('asReferenceList: 非数组一律返回空数组', () => {
  assert.deepEqual(asReferenceList(null), [])
  assert.deepEqual(asReferenceList(undefined), [])
  assert.deepEqual(asReferenceList('oops'), [])
  assert.deepEqual(asReferenceList({ title: '不是数组' }), [])
})

test('asReferenceList: 过滤 null / 空对象 / 数组等坏条目', () => {
  const rows = asReferenceList([
    null,
    undefined,
    {},
    [],
    '字符串条目',
    { title: '中华人民共和国劳动合同法' },
    { source_id: 2 },
    { citation: '第四十七条…' },
  ])
  assert.equal(rows.length, 3)
  assert.deepEqual(
    rows.map((row) => row.title || row.source_id || 'citation'),
    ['中华人民共和国劳动合同法', 2, 'citation'],
  )
})

test('asReferenceList: 正常引用原样保留（不复制、不改字段）', () => {
  const reference = { source_id: 71, title: '中华人民共和国劳动合同法', version: '2021-01-01', verification: { verified: true } }
  const rows = asReferenceList([reference])
  assert.equal(rows.length, 1)
  assert.equal(rows[0], reference)
})
