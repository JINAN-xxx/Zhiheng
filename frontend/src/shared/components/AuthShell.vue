<!-- Adapted from the original RuoYi / 智衡 frontend, MIT licensed. -->
<template>
  <main class="auth-shell" :class="{ 'auth-shell--wide': wide, 'auth-shell--split': split }">
    <div v-if="split" class="auth-shell__left">
      <BrandMark class="auth-shell__left-logo" />
    </div>

    <header v-if="!split" class="auth-shell__brand" aria-label="智衡">
      <BrandMark class="auth-shell__logo" />
    </header>

    <section class="auth-shell__panel">
      <div v-if="eyebrow || title || description" class="auth-shell__heading">
        <span v-if="eyebrow" class="auth-shell__eyebrow">{{ eyebrow }}</span>
        <h1 v-if="title">{{ title }}</h1>
        <p v-if="description">{{ description }}</p>
      </div>
      <slot />
    </section>

    <footer v-if="$slots.footer" class="auth-shell__footer">
      <slot name="footer" />
    </footer>
  </main>
</template>

<script setup lang="ts">
import BrandMark from './BrandMark.vue'

defineProps({
  brand: {
    type: String,
    default: '智衡'
  },
  eyebrow: {
    type: String,
    default: ''
  },
  title: {
    type: String,
    default: ''
  },
  description: {
    type: String,
    default: ''
  },
  wide: {
    type: Boolean,
    default: false
  },
  split: {
    type: Boolean,
    default: false
  }
})
</script>

<style scoped>
.auth-shell {
  position: relative;
  box-sizing: border-box;
  display: flex;
  min-height: 100vh;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 84px 24px 64px;
  overflow-x: hidden;
  background: var(--bg-page);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}

.auth-shell--split {
  padding: 0;
}

.auth-shell__left {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  width: 33.333%;
  height: 100vh;
  pointer-events: none;
}

.auth-shell__left-logo {
  height: 48px;
  width: auto;
  object-fit: contain;
}

.auth-shell__brand {
  position: absolute;
  top: 28px;
  left: 32px;
  display: flex;
  align-items: center;
}

.auth-shell__logo {
  height: 36px;
  width: auto;
  object-fit: contain;
}

.auth-shell__brand-text {
  margin-left: 10px;
  font-size: 18px;
  font-weight: var(--workspace-font-weight-semibold);
  white-space: nowrap;
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}

.auth-shell__panel {
  width: min(100%, 420px);
  padding: 32px;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.auth-shell--split .auth-shell__panel {
  width: min(100%, 420px);
}

.auth-shell--wide .auth-shell__panel {
  width: min(100%, 680px);
}

.auth-shell__heading {
  margin-bottom: 28px;
}

.auth-shell__eyebrow {
  color: var(--main-color);
  font-size: 12px;
  font-weight: var(--workspace-font-weight-semibold);
}

.auth-shell__heading h1 {
  margin: 7px 0 8px;
  color: var(--color-text);
  font-size: 26px;
  font-weight: var(--workspace-font-weight-semibold);
  line-height: 1.3;
}

.auth-shell__heading p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.65;
}

.auth-shell__footer {
  position: absolute;
  right: 24px;
  bottom: 20px;
  left: 24px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.auth-shell--split .auth-shell__footer {
  position: relative;
  right: auto;
  bottom: auto;
  left: auto;
  margin-top: 24px;
}

@media (max-width: 560px) {
  .auth-shell {
    align-items: start;
    padding: 84px 16px 64px;
  }

  .auth-shell__left {
    position: relative;
    width: 100%;
    height: auto;
    padding: 24px 0;
  }

  .auth-shell__brand {
    top: 24px;
    left: 20px;
  }

  .auth-shell__panel {
    padding: 24px 20px;
  }
}
</style>
