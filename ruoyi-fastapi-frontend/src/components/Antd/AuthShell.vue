<template>
  <main class="auth-shell" :class="{ 'auth-shell--wide': wide }">
    <header class="auth-shell__brand" aria-label="智衡 AI">
      <span class="auth-shell__mark" aria-hidden="true">智</span>
      <span>{{ brand }}</span>
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

<script setup>
defineProps({
  brand: {
    type: String,
    default: '智衡 AI',
  },
  eyebrow: {
    type: String,
    default: '',
  },
  title: {
    type: String,
    default: '',
  },
  description: {
    type: String,
    default: '',
  },
  wide: {
    type: Boolean,
    default: false,
  },
})
</script>

<style scoped>
.auth-shell {
  position: relative;
  display: grid;
  min-height: 100%;
  place-items: center;
  padding: 84px 24px 64px;
  overflow-x: hidden;
  background: var(--workspace-bg);
  color: var(--workspace-text);
  font-family: var(--workspace-font-family);
}

.auth-shell::before {
  position: absolute;
  inset: 0 0 auto;
  height: 4px;
  background: var(--workspace-main);
  content: '';
}

.auth-shell__brand {
  position: absolute;
  top: 28px;
  left: 32px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--workspace-text);
  font-size: 16px;
  font-weight: 650;
}

.auth-shell__mark {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: var(--workspace-radius-sm);
  background: var(--workspace-main);
  color: #fff;
  font-size: 15px;
}

.auth-shell__panel {
  width: min(100%, 420px);
  padding: 32px;
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-md);
  background: var(--workspace-surface);
  box-shadow: var(--workspace-shadow-float);
}

.auth-shell--wide .auth-shell__panel {
  width: min(100%, 680px);
}

.auth-shell__heading {
  margin-bottom: 28px;
}

.auth-shell__eyebrow {
  color: var(--workspace-main-deep);
  font-size: 12px;
  font-weight: 650;
}

.auth-shell__heading h1 {
  margin: 7px 0 8px;
  color: var(--workspace-text);
  font-size: 26px;
  font-weight: 650;
  line-height: 1.3;
}

.auth-shell__heading p {
  margin: 0;
  color: var(--workspace-text-secondary);
  font-size: 14px;
  line-height: 1.65;
}

.auth-shell__footer {
  position: absolute;
  right: 24px;
  bottom: 20px;
  left: 24px;
  color: var(--workspace-text-muted);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 560px) {
  .auth-shell {
    align-items: start;
    padding: 84px 16px 64px;
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
