import test from 'node:test'
import assert from 'node:assert/strict'

import { clampNumber, formatNumberInput, parseNumberInput } from './numberField.js'

test('parseNumberInput：空值与非法字符返回 null', () => {
  assert.equal(parseNumberInput(''), null)
  assert.equal(parseNumberInput('   '), null)
  assert.equal(parseNumberInput(null), null)
  assert.equal(parseNumberInput('abc'), null)
  assert.equal(parseNumberInput('1a'), null)
})

test('parseNumberInput：整数、小数、负数与半截输入', () => {
  assert.equal(parseNumberInput(' 90 '), 90)
  assert.equal(parseNumberInput('90.5'), 90.5)
  assert.equal(parseNumberInput('-3'), -3)
  assert.equal(parseNumberInput('1.'), 1)
  assert.equal(parseNumberInput('，1,200'), 1200)
})

test('clampNumber：按小数位与上下限归一', () => {
  assert.equal(clampNumber('90.456', { precision: 2 }), 90.46)
  assert.equal(clampNumber(0, { min: 1 }), 1)
  assert.equal(clampNumber(2000, { min: 1, max: 1440 }), 1440)
  assert.equal(clampNumber(2, { min: 2, max: 10 }), 2)
  assert.equal(clampNumber('', { min: 1 }), null)
  assert.equal(clampNumber('abc'), null)
})

test('clampNumber：precision=0 取整', () => {
  assert.equal(clampNumber(90.6), 91)
  assert.equal(clampNumber('90.4'), 90)
})

test('formatNumberInput：整数与小数显示', () => {
  assert.equal(formatNumberInput(90), '90')
  assert.equal(formatNumberInput(90, 2), '90.00')
  assert.equal(formatNumberInput(90.456, 2), '90.46')
  assert.equal(formatNumberInput(null), '')
  assert.equal(formatNumberInput(''), '')
  assert.equal(formatNumberInput('abc'), '')
})
