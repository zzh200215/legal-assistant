import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { useDocumentQaFeedback } from '../useDocumentQaFeedback.js'

test('submits a document question and refreshes the record list', async () => {
  const calls = []
  const messages = []
  const documentId = ref(12)
  const feedback = useDocumentQaFeedback({
    client: {
      askDocument: async (id, question) => {
        calls.push([id, question])
        return {
          data: {
            qa_record_id: 8,
            answer: 'answer',
            citations: [{ document_id: id }],
            confidence: 0.9,
          },
        }
      },
      submitQaFeedback: async () => ({ data: { feedback_value: 'positive', feedback_status: 'submitted' } }),
    },
    message: { success: (value) => messages.push(value), error: (value) => { throw new Error(value) } },
    documentId,
    refreshRecords: async () => calls.push(['refresh']),
  })

  feedback.qaQuestion.value = 'What does this clause require?'
  await feedback.askDocumentQuestion()

  assert.deepEqual(calls, [[12, 'What does this clause require?'], ['refresh']])
  assert.equal(feedback.qaQuestion.value, '')
  assert.equal(feedback.qaResult.value.qa_record_id, 8)
  assert.equal(feedback.qaResult.value.can_answer, true)

  await feedback.submitPositiveFeedback()
  assert.equal(feedback.qaResult.value.feedback_status, 'submitted')
  assert.equal(messages.length, 1)
})

test('does not submit an empty question', async () => {
  let called = false
  const feedback = useDocumentQaFeedback({
    client: { askDocument: async () => { called = true } },
    message: { success: () => {}, error: () => {} },
    documentId: ref(7),
    refreshRecords: async () => {},
  })

  await feedback.askDocumentQuestion()
  assert.equal(called, false)
})
