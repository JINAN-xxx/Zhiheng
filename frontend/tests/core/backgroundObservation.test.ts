import assert from 'node:assert/strict'
import { test } from 'node:test'
import { waitForBackgroundObservation } from '../../src/shared/backgroundObservation.ts'

class Visibility {
  hidden = false
  listeners = new Set<() => void>()
  addEventListener(_type: 'visibilitychange', listener: () => void): void {
    this.listeners.add(listener)
  }
  removeEventListener(_type: 'visibilitychange', listener: () => void): void {
    this.listeners.delete(listener)
  }
  setHidden(hidden: boolean): void {
    this.hidden = hidden
    for (const listener of this.listeners) listener()
  }
}

void test('visible observation waits for its interval and releases its listeners', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const visibility = new Visibility()
  let completed = false
  const waiting = waitForBackgroundObservation(new AbortController().signal, 1500, visibility).then(
    () => {
      completed = true
    }
  )
  t.mock.timers.tick(1499)
  await Promise.resolve()
  assert.equal(completed, false)
  t.mock.timers.tick(1)
  await waiting
  assert.equal(completed, true)
  assert.equal(visibility.listeners.size, 0)
})

void test('hiding during a wait pauses observation and returning observes immediately', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const visibility = new Visibility()
  let completed = false
  const waiting = waitForBackgroundObservation(new AbortController().signal, 1500, visibility).then(
    () => {
      completed = true
    }
  )
  t.mock.timers.tick(500)
  visibility.setHidden(true)
  t.mock.timers.tick(30_000)
  await Promise.resolve()
  assert.equal(completed, false)
  visibility.setHidden(false)
  await waiting
  assert.equal(completed, true)
  assert.equal(visibility.listeners.size, 0)
})

void test('an initially hidden observation waits for visibility and abort clears the wait', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const visibility = new Visibility()
  visibility.hidden = true
  const controller = new AbortController()
  const waiting = waitForBackgroundObservation(controller.signal, 1500, visibility)
  const rejected = assert.rejects(waiting, { name: 'AbortError' })
  t.mock.timers.tick(30_000)
  controller.abort()
  await rejected
  assert.equal(visibility.listeners.size, 0)
  visibility.setHidden(false)
  t.mock.timers.tick(30_000)
})

void test('server observation can use an ordinary timer without a document', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const waiting = waitForBackgroundObservation(new AbortController().signal, 20)
  t.mock.timers.tick(20)
  await waiting
  const controller = new AbortController()
  controller.abort('leaving')
  await assert.rejects(waitForBackgroundObservation(controller.signal), { name: 'AbortError' })
})
