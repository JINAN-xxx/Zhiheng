<!-- Adapted from the original RuoYi / 智衡 frontend, MIT licensed. -->
<script setup lang="ts">
import BrandMark from './BrandMark.vue'

defineProps<{ title: string; description?: string }>()
</script>

<template>
  <main class="auth-shell">
    <header class="auth-shell__brand" aria-label="智衡">
      <BrandMark />
    </header>

    <div class="auth-shell__layout">
      <div class="auth-shell__visual" aria-hidden="true">
        <div class="auth-shell__artwork">
          <svg class="auth-shell__orbits" viewBox="0 0 440 400" fill="none">
            <ellipse
              class="auth-shell__orbit"
              cx="220"
              cy="200"
              rx="194"
              ry="131"
              transform="rotate(-28 220 200)"
            />
            <ellipse
              class="auth-shell__orbit auth-shell__orbit--accent"
              cx="220"
              cy="200"
              rx="194"
              ry="131"
              transform="rotate(28 220 200)"
            />
            <circle class="auth-shell__halo" cx="220" cy="200" r="98" />
            <circle class="auth-shell__node" cx="62" cy="128" r="7" />
            <circle class="auth-shell__node auth-shell__node--accent" cx="378" cy="272" r="7" />
            <circle class="auth-shell__node auth-shell__node--small" cx="290" cy="55" r="4" />
            <circle class="auth-shell__node auth-shell__node--small" cx="150" cy="345" r="4" />
          </svg>
          <BrandMark compact class="auth-shell__symbol" />
        </div>
        <p class="auth-shell__caption">
          <span>对话</span><span class="auth-shell__dot">·</span><span>智能体</span
          ><span class="auth-shell__dot">·</span><span>知识库</span>
        </p>
      </div>

      <section class="auth-shell__panel" aria-labelledby="auth-title">
        <div class="auth-shell__heading">
          <h1 id="auth-title">{{ title }}</h1>
          <p v-if="description">{{ description }}</p>
        </div>
        <slot />
      </section>
    </div>
  </main>
</template>

<style scoped>
.auth-shell {
  display: flex;
  min-height: 100vh;
  min-height: 100svh;
  flex-direction: column;
  padding: 32px 48px;
  background: var(--bg-page);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}

.auth-shell__brand {
  display: flex;
  flex-shrink: 0;
  align-items: center;
}

.auth-shell__layout {
  display: grid;
  width: min(100%, 1120px);
  flex: 1;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  align-items: center;
  gap: clamp(64px, 8vw, 128px);
  margin: 0 auto;
  padding: 48px 0 92px;
}

.auth-shell__visual {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  gap: 24px;
}

.auth-shell__artwork {
  position: relative;
  display: grid;
  width: min(100%, 440px);
  aspect-ratio: 1.1;
  place-items: center;
}

.auth-shell__orbits {
  position: absolute;
  width: 100%;
  height: 100%;
}

.auth-shell__orbit {
  stroke: var(--gray-150);
  stroke-width: 1;
}

.auth-shell__orbit--accent {
  stroke: var(--main-100);
}

.auth-shell__halo {
  fill: var(--main-30);
}

.auth-shell__node {
  fill: var(--bg-page);
  stroke: var(--main-200);
  stroke-width: 2;
}

.auth-shell__node--accent {
  fill: var(--main-color);
  stroke: var(--main-color);
}

.auth-shell__node--small {
  fill: var(--main-200);
  stroke: none;
}

.auth-shell__symbol {
  position: relative;
}

.auth-shell__symbol :deep(svg) {
  width: clamp(96px, 12vw, 136px);
  height: clamp(96px, 12vw, 136px);
  stroke-width: 2;
}

.auth-shell__caption {
  display: flex;
  align-items: center;
  gap: 18px;
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.5;
}

.auth-shell__dot {
  color: var(--gray-300);
}

.auth-shell__panel {
  width: 100%;
  max-width: 380px;
  min-width: 0;
  justify-self: center;
}

.auth-shell__heading {
  margin-bottom: 32px;
}

.auth-shell__heading h1 {
  margin: 0;
  font-size: 30px;
  font-weight: var(--workspace-font-weight-semibold);
  line-height: 1.4;
}

.auth-shell__heading p {
  margin: 10px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .auth-shell {
    padding: 24px;
  }

  .auth-shell__layout {
    grid-template-columns: minmax(0, 1fr);
    padding: 48px 0 88px;
  }

  .auth-shell__visual {
    display: none;
  }
}

@media (max-width: 480px) {
  .auth-shell {
    padding: 24px 20px;
  }

  .auth-shell__layout {
    padding-bottom: 64px;
  }
}
</style>
