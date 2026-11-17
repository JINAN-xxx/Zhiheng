<script setup lang="ts">
import { useId } from 'vue'
withDefaults(defineProps<{ animated?: boolean; expression?: 'idle' | 'happy' }>(), {
  animated: false,
  expression: 'idle'
})
const id = useId()
</script>
<template>
  <svg
    class="xiaoheng-character"
    :class="{ 'is-animated': animated }"
    viewBox="0 0 240 270"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <defs>
      <linearGradient
        :id="`${id}-body`"
        x1="76"
        y1="165"
        x2="157"
        y2="230"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="white" />
        <stop offset="1" stop-color="#F0EDFF" />
      </linearGradient>
      <linearGradient
        :id="`${id}-face`"
        x1="72"
        y1="84"
        x2="172"
        y2="151"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#7569F3" />
        <stop offset="1" stop-color="#5548DD" />
      </linearGradient>
    </defs>
    <ellipse class="mascot-shadow" cx="120" cy="249" rx="54" ry="7" fill="#EFECFC" />
    <g class="mascot-body">
      <path d="M120 54V38" stroke="#B4A9F7" stroke-width="5" stroke-linecap="round" />
      <path d="m120 22 9 10-9 10-9-10Z" fill="#A89BF7" />
      <path d="M120 24v13" stroke="#DCD4FF" stroke-width="2" stroke-linecap="round" />
      <path d="m82 184-15 17" stroke="#D5CFF8" stroke-width="19" stroke-linecap="round" />
      <path d="m82 182-15 17" stroke="white" stroke-width="15" stroke-linecap="round" />
      <rect
        x="58"
        y="193"
        width="17"
        height="24"
        rx="8.5"
        transform="rotate(22 66 205)"
        fill="#8E83F5"
      />
      <g class="wave-arm">
        <path
          d="M161 184q48 7 55-34"
          fill="none"
          stroke="#D5CFF8"
          stroke-width="19"
          stroke-linecap="round"
        />
        <path
          d="M161 182q47 7 53-34"
          fill="none"
          stroke="white"
          stroke-width="15"
          stroke-linecap="round"
        />
        <rect
          x="207"
          y="126"
          width="17"
          height="26"
          rx="8.5"
          transform="rotate(10 215 139)"
          fill="#8E83F5"
        />
        <path d="m216 130 1-9" stroke="#8E83F5" stroke-width="6" stroke-linecap="round" />
      </g>
      <rect x="90" y="215" width="22" height="25" rx="10" fill="#8E83F5" />
      <rect x="129" y="215" width="22" height="25" rx="10" fill="#8E83F5" />
      <rect x="87" y="230" width="27" height="12" rx="6" fill="#DDD6FF" />
      <rect x="127" y="230" width="27" height="12" rx="6" fill="#DDD6FF" />
      <rect
        x="80"
        y="166"
        width="80"
        height="63"
        rx="25"
        :fill="`url(#${id}-body)`"
        stroke="#D5CEF7"
        stroke-width="2"
      />
      <rect x="100" y="184" width="40" height="27" rx="10" fill="#F0EDFF" />
      <path
        d="M112 191h17l-16 13h17m-21-6h9"
        stroke="#8274EE"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <g class="character-head">
        <rect x="31" y="91" width="19" height="43" rx="9.5" fill="#9B8EF3" />
        <rect x="190" y="91" width="19" height="43" rx="9.5" fill="#9B8EF3" />
        <rect
          x="42"
          y="53"
          width="156"
          height="119"
          rx="44"
          fill="white"
          stroke="#D5CEF7"
          stroke-width="2.5"
        />
        <path d="M63 71q9-9 25-9" stroke="#F4F2FE" stroke-width="5" stroke-linecap="round" />
        <rect x="59" y="79" width="122" height="71" rx="26" :fill="`url(#${id}-face)`" />
        <path
          v-if="expression === 'happy'"
          d="M88 112q7-13 14 0m31 0q7-13 14 0"
          stroke="white"
          stroke-width="6"
          stroke-linecap="round"
        />
        <g v-else class="character-eyes">
          <rect x="89" y="100" width="11" height="18" rx="5.5" fill="white" />
          <rect x="135" y="100" width="11" height="18" rx="5.5" fill="white" />
        </g>
        <path
          d="M109 127q10 8 20 0"
          fill="none"
          stroke="white"
          stroke-width="3.5"
          stroke-linecap="round"
        />
        <rect x="75" y="125" width="12" height="5" rx="2.5" fill="#BCB2FF" opacity=".65" />
        <rect x="153" y="125" width="12" height="5" rx="2.5" fill="#BCB2FF" opacity=".65" />
      </g>
    </g>
  </svg>
</template>
<style scoped>
.xiaoheng-character {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
}
.is-animated .mascot-body {
  animation: character-breathe 3.8s ease-in-out infinite;
}
.is-animated .character-head {
  transform-origin: 120px 164px;
  animation: character-look 9s ease-in-out infinite;
}
@keyframes character-look {
  0%,
  72%,
  100% {
    transform: rotate(0);
  }
  80% {
    transform: rotate(-4deg);
  }
  90% {
    transform: rotate(3deg);
  }
}
.is-animated .character-eyes {
  transform-box: fill-box;
  transform-origin: center;
  animation: character-blink 6.4s linear infinite;
}
.is-animated .mascot-shadow {
  transform-origin: 120px 249px;
  animation: character-shadow 3.8s ease-in-out infinite;
}
@keyframes character-breathe {
  50% {
    transform: translateY(-4px);
  }
}
@keyframes character-shadow {
  50% {
    transform: scaleX(0.9);
    opacity: 0.7;
  }
}
@keyframes character-blink {
  0%,
  43%,
  47%,
  100% {
    transform: scaleY(1);
  }
  45% {
    transform: scaleY(0.12);
  }
}
@media (prefers-reduced-motion: reduce) {
  .xiaoheng-character * {
    animation: none !important;
  }
}
</style>
