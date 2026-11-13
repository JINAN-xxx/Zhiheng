import assert from 'node:assert/strict'
import test from 'node:test'
import { ApiError } from '../../src/shared/http.ts'
import { inputIssueFrom } from '../../src/modules/chat/inputIssue.ts'

void test('configured capacity failures explain the limit and preserve user input', () => {
  const issue = inputIssueFrom(
    new ApiError('context_input_too_long', 'internal message', 422, {
      estimated_input_tokens: 12000,
      input_budget: 8192
    })
  )
  assert.ok(issue)
  assert.match(issue.description, /附件和草稿已保留/)
  assert.match(issue.statistics, /12,000/)
  assert.match(issue.statistics, /8,192/)
})
void test('invalid diagnostics and unknown network results are not treated as safe capacity retries', () => {
  assert.equal(inputIssueFrom(new TypeError('network')), null)
  assert.equal(inputIssueFrom(new ApiError('unavailable', 'retry', 503)), null)
  assert.equal(inputIssueFrom(new ApiError('context_input_too_long', 'unknown', 503)), null)
  assert.equal(
    inputIssueFrom(new ApiError('context_input_too_long', 'long', 422, { input_budget: 'bad' }))
      ?.statistics,
    ''
  )
})
