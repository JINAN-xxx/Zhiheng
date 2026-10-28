import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createSpeechPlayback } from '../../src/shared/speechPlayback.ts'
import type {
  SpeechCallbacks,
  SpeechPlaybackPort,
  SpeechState
} from '../../src/shared/speechPlayback.ts'

function setup(available = true) {
  const callbacks: SpeechCallbacks[] = []
  let cancels = 0
  const port: SpeechPlaybackPort = {
    available: () => available,
    start: (_text, events) => {
      callbacks.push(events)
    },
    cancel: () => {
      cancels += 1
    }
  }
  return { playback: createSpeechPlayback(port), callbacks, cancels: () => cancels }
}
void test('one owner plays at a time and late completion cannot stop its replacement', () => {
  const { playback, callbacks } = setup()
  const first: SpeechState[] = [],
    second: SpeechState[] = []
  playback.toggle('first', 'one', (state) => first.push(state), assert.fail)
  callbacks[0]!.start()
  playback.toggle('second', 'two', (state) => second.push(state), assert.fail)
  callbacks[1]!.start()
  callbacks[0]!.end()
  assert.deepEqual(first, ['starting', 'playing', 'idle'])
  assert.deepEqual(second, ['starting', 'playing'])
  playback.stop('first')
  assert.deepEqual(second, ['starting', 'playing'])
  playback.stop('second')
  assert.equal(second.at(-1), 'idle')
})
void test('same owner toggles off; unsupported and synthesis errors are reported truthfully', () => {
  const { playback, callbacks, cancels } = setup()
  const states: SpeechState[] = [],
    errors: string[] = []
  playback.toggle(
    'a',
    'hello',
    (state) => states.push(state),
    (error) => errors.push(error)
  )
  playback.toggle(
    'a',
    'hello',
    (state) => states.push(state),
    (error) => errors.push(error)
  )
  assert.equal(states.at(-1), 'idle')
  assert.ok(cancels() > 0)
  playback.toggle(
    'a',
    'hello',
    (state) => states.push(state),
    (error) => errors.push(error)
  )
  callbacks.at(-1)!.error('audio-busy')
  assert.equal(states.at(-1), 'idle')
  assert.match(errors[0]!, /audio-busy/)
  const missing = setup(false)
  missing.playback.toggle('a', 'hello', assert.fail, (error) => errors.push(error))
  assert.match(errors.at(-1)!, /不支持/)
})
