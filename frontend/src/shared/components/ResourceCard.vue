<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    status?: string
    tone?: 'positive' | 'neutral' | 'processing' | 'warning' | 'danger'
    iconTone?: 'primary' | 'green' | 'amber' | 'blue' | 'neutral'
    interactive?: boolean
    openLabel?: string
    compact?: boolean
    selected?: boolean
  }>(),
  {
    subtitle: '',
    status: '',
    tone: 'neutral',
    iconTone: 'primary',
    interactive: false,
    openLabel: '',
    compact: false,
    selected: false
  }
)
defineEmits<{ open: [] }>()
</script>

<template>
  <article
    class="resource-card"
    :class="{ 'resource-card--compact': compact, 'resource-card--selected': selected }"
  >
    <header class="resource-card__header">
      <span
        v-if="$slots.icon"
        class="resource-card__icon"
        :class="`is-${iconTone}`"
        aria-hidden="true"
      >
        <slot name="icon" />
      </span>
      <div class="resource-card__identity">
        <div class="resource-card__title-row">
          <h2 class="resource-card__title" :title="title">
            <button
              v-if="interactive"
              type="button"
              class="resource-card__title-button"
              :aria-label="openLabel || `查看${title}`"
              @click="$emit('open')"
            >
              {{ title }}
            </button>
            <span v-else>{{ title }}</span>
          </h2>
          <slot name="title-action" />
        </div>
        <span v-if="subtitle" class="resource-card__subtitle">{{ subtitle }}</span>
      </div>
      <span v-if="status" class="resource-card__status" :class="`is-${tone}`">{{ status }}</span>
    </header>
    <div v-if="$slots.default" class="resource-card__body"><slot /></div>
    <footer v-if="$slots['footer-info'] || $slots.actions" class="resource-card__footer">
      <div class="resource-card__footer-info"><slot name="footer-info" /></div>
      <div v-if="$slots.actions" class="resource-card__actions"><slot name="actions" /></div>
    </footer>
  </article>
</template>

<style scoped>
.resource-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 20px;
  border: 1px solid var(--gray-150, #e9eaf1);
  border-radius: 10px;
  background: var(--bg-container, #fff);
  color: var(--color-text, #282c3c);
  font-family: var(--workspace-font-family);
  transition: border-color 0.16s;
}
.resource-card--selected {
  border-color: var(--main-200, #d5ccff);
  background: var(--main-50, #fcfbff);
}
.resource-card__header {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}
.resource-card__icon {
  width: 36px;
  height: 36px;
  display: grid;
  flex-shrink: 0;
  place-items: center;
  border-radius: 9px;
  background: var(--main-50, #f2efff);
  color: var(--main-color, #6554f6);
  font-size: 20px;
}
.resource-card__icon.is-green {
  background: #edf7f0;
  color: #45975d;
}
.resource-card__icon.is-amber {
  background: #fbf5e8;
  color: #a77d29;
}
.resource-card__icon.is-blue {
  background: #f0f3fc;
  color: #6581bb;
}
.resource-card__icon.is-neutral {
  background: #f4f5f8;
  color: #858b9e;
}
.resource-card__identity {
  min-width: 0;
  flex: 1;
}
.resource-card__title-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.resource-card__title {
  min-width: 0;
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 24px;
}
.resource-card__title > span,
.resource-card__title-button {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__title-button {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.resource-card__title-button:hover {
  color: var(--main-color, #6554f6);
}
.resource-card__title-button:focus-visible,
.resource-card :deep(.resource-id-copy:focus-visible) {
  outline: 2px solid var(--main-300, #a99aff);
  outline-offset: 3px;
  border-radius: 3px;
}
.resource-card__subtitle {
  display: block;
  margin-top: 3px;
  color: var(--color-text-secondary, #818799);
  font-size: 12px;
  line-height: 18px;
}
.resource-card__status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  padding: 0 7px;
  border-radius: 4px;
  background: #f5f5f8;
  color: #74798c;
  font-size: 12px;
  line-height: 21px;
  white-space: nowrap;
}
.resource-card__status::before {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  content: '';
}
.resource-card__status.is-positive {
  color: #217a3e;
  background: #eff8f1;
}
.resource-card__status.is-processing {
  color: #4b6ee8;
  background: #eff3ff;
}
.resource-card__status.is-warning {
  color: #9c711f;
  background: #fff7e8;
}
.resource-card__status.is-danger {
  color: #bf4b59;
  background: #fff0f1;
}
.resource-card__body {
  min-width: 0;
  min-height: 74px;
  margin: 19px 0 17px;
  color: var(--color-text-secondary, #70788e);
  font-size: 13px;
  line-height: 22px;
}
.resource-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 43px;
  margin-top: auto;
  padding-top: 13px;
  border-top: 1px solid var(--gray-100, #f0f0f5);
}
.resource-card__footer-info {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text-secondary, #858b9e);
  font-size: 12px;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.resource-card :deep(.resource-id-copy) {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  padding: 3px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #9197a7;
  font-size: 13px;
  cursor: pointer;
}
.resource-card :deep(.resource-id-copy:hover) {
  background: var(--main-50, #f4f1ff);
  color: var(--main-color, #6554f6);
}
.resource-card :deep(.resource-card-primary) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--main-color, #6554f6);
  font-size: 13px;
  font-weight: 500;
  line-height: 22px;
  cursor: pointer;
}
.resource-card :deep(.resource-card-primary:disabled) {
  color: var(--color-text-disabled, #a1a5b2);
  cursor: not-allowed;
}
.resource-card--compact {
  padding: 15px 16px;
}
.resource-card--compact .resource-card__header {
  gap: 10px;
}
.resource-card--compact .resource-card__icon {
  width: 32px;
  height: 32px;
  font-size: 18px;
}
.resource-card--compact .resource-card__body {
  min-height: 0;
  margin: 12px 0;
}
.resource-card--compact .resource-card__footer {
  min-height: 32px;
  padding-top: 10px;
}
@media (hover: hover) {
  .resource-card:hover {
    border-color: var(--main-200, #d4cff6);
  }
}
@media (max-width: 600px) {
  .resource-card {
    padding: 18px;
  }
  .resource-card--compact {
    padding: 13px 14px;
  }
  .resource-card__header {
    gap: 10px;
  }
  .resource-card__title {
    font-size: 15px;
  }
  .resource-card__actions {
    gap: 5px;
  }
}
</style>
