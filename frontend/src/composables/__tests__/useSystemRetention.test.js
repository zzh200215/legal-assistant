import assert from 'node:assert/strict'
import test from 'node:test'
import { useSystemRetention } from '../useSystemRetention.js'

test('fetchRetention loads cohorts from the admin retention API', async () => {
  const calls = []
  const retention = useSystemRetention({
    client: {
      dashboardRetention: async (days) => {
        calls.push(['retention', days])
        return { data: { data: { cohorts: [{ week_start: '2026-07-20', cohort_size: 3, d7: { rate: 0.5, active: 1, observed: 2 } }] } } }
      },
    },
    message: { success: () => {}, error: () => {} },
  })

  retention.retentionDays.value = 30
  await retention.fetchRetention()

  assert.deepEqual(calls, [['retention', 30]])
  assert.equal(retention.retentionLoading.value, false)
  assert.equal(retention.retentionData.value.cohorts[0].cohort_size, 3)
})

test('fetchNorthStar loads weekly buckets from the admin north-star API', async () => {
  const calls = []
  const northStar = useSystemRetention({
    client: {
      dashboardNorthStar: async (weeks) => {
        calls.push(['north-star', weeks])
        return { data: { data: { current: { active_lawyers: 2, tasks: 3 }, weekly: [{ week_start: '2026-08-03', active_lawyers: 2 }] } } }
      },
    },
    message: { success: () => {}, error: () => {} },
  })

  northStar.northStarWeeks.value = 4
  await northStar.fetchNorthStar()

  assert.deepEqual(calls, [['north-star', 4]])
  assert.equal(northStar.northStarData.value.current.active_lawyers, 2)
  assert.equal(northStar.northStarData.value.weekly.length, 1)
})

test('clears data and reports an error when the API call fails', async () => {
  const errors = []
  const retention = useSystemRetention({
    client: { dashboardRetention: async () => { throw { response: { data: { detail: 'boom' } } } } },
    message: { success: () => {}, error: (value) => errors.push(value) },
  })

  await retention.fetchRetention()

  assert.equal(retention.retentionData.value, null)
  assert.equal(retention.retentionLoading.value, false)
  assert.deepEqual(errors, ['boom'])
})
