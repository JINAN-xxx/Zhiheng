import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message as notice } from 'ant-design-vue'
import { createRequestId } from '../../../shared/requestId.ts'
import {
  ApiError,
  errorMessage,
  isAborted,
  isDefiniteRejection,
  RequestScope
} from '../../../shared/http.ts'
import { observeRun } from '../../../shared/runs.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { startModelTest } from '../api/index.ts'
import type { ChatModel } from '../api/index.ts'
import type { ModelAccess } from './useModelAccess.ts'

interface ModelConnectionTestOptions {
  access: ModelAccess
  pending: Record<string, boolean>
  failures: Record<string, { message: string; details?: unknown } | undefined>
  connectionDisabled: (id: string) => boolean
}
export function useModelConnectionTest(options: ModelConnectionTestOptions) {
  const { canTestModels, denyTrialAccess } = options.access
  const modelPending = options.pending
  const modelFailures = options.failures
  const connectionDisabled = options.connectionDisabled
  const testRun = ref<RunSnapshot | null>(null)
  const testingModel = ref('')
  const testDisconnected = ref(false)
  const testScope = new RequestScope()
  let testIntent: { modelId: string; requestId: string } | null = null
  let disposed = false
  const testBusy = computed(
    () =>
      testingModel.value !== '' &&
      !testDisconnected.value &&
      (testRun.value === null || !isTerminal(testRun.value))
  )
  async function followTest(
    initial: RunSnapshot,
    context: ReturnType<RequestScope['current']>
  ): Promise<RunSnapshot> {
    return observeRun(
      initial,
      (run) => {
        if (context.isCurrent() && acceptSnapshot(testRun.value, run)) testRun.value = run
      },
      context.signal
    )
  }
  async function testModel(model: ChatModel): Promise<void> {
    if (disposed) return
    if (
      (testIntent && testIntent.modelId !== model.id) ||
      (testDisconnected.value &&
        testRun.value &&
        !isTerminal(testRun.value) &&
        testingModel.value !== model.id)
    ) {
      void notice.info('请先确认上次连接测试结果，再测试其他模型')
      return
    }
    if (
      testBusy.value ||
      modelPending[model.id] ||
      !canTestModels.value ||
      !model.enabled ||
      connectionDisabled(model.connection_id)
    )
      return
    const confirming = testIntent !== null
    const context = testScope.replace()
    const previousRun =
      testDisconnected.value && testingModel.value === model.id ? testRun.value : null
    testingModel.value = model.id
    testRun.value = previousRun
    testDisconnected.value = false
    delete modelFailures[model.id]
    try {
      let run = previousRun
      if (!run) {
        const intent =
          testIntent?.modelId === model.id
            ? testIntent
            : { modelId: model.id, requestId: createRequestId() }
        testIntent = intent
        run = await startModelTest(model.id, intent.requestId, context.signal)
      }
      if (!context.isCurrent()) return
      testIntent = null
      testRun.value = run
      const result = await followTest(run, context)
      if (!context.isCurrent()) return
      if (result.status === 'completed') void notice.success('测试成功')
      else {
        const message = result.error?.message || '测试失败'
        modelFailures[model.id] = { message }
        void notice.error(message)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        if (reason instanceof ApiError && reason.status === 403 && reason.code === 'forbidden')
          denyTrialAccess()
        if (isDefiniteRejection(reason) && !confirming) testIntent = null
        testDisconnected.value = testRun.value !== null && !isTerminal(testRun.value)
        if (!testRun.value) testingModel.value = ''
        const message = testDisconnected.value
          ? '测试结果获取失败，请再次点击测试查看结果'
          : errorMessage(reason)
        modelFailures[model.id] = {
          message,
          details: reason instanceof ApiError ? reason.details : undefined
        }
        void notice.error(message)
      }
    }
  }

  function resetTest(): void {
    testRun.value = null
    testingModel.value = ''
    testIntent = null
    testDisconnected.value = false
  }
  watch([options.access.authority, canTestModels] as const, ([current], [previous]) => {
    testScope.replace()
    if (current?.id !== previous?.id) resetTest()
    else
      testDisconnected.value = Boolean(testIntent || (testRun.value && !isTerminal(testRun.value)))
  })
  onBeforeUnmount(() => {
    disposed = true
    testScope.dispose()
    resetTest()
  })
  return { testingModel, testBusy, testModel }
}
