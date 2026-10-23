import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const coreFiles = ['src/{app,shared,modules}/**/*.{ts,tsx,vue}', 'src/vite-env.d.ts']

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    ...js.configs.recommended,
    files: ['eslint.config.js'],
    languageOptions: { globals: globals.node }
  },
  {
    files: coreFiles,
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        project: './tsconfig.json',
        tsconfigRootDir: import.meta.dirname,
        extraFileExtensions: ['.vue']
      }
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }]
    }
  },
  {
    files: ['src/{app,shared,modules}/**/*.vue'],
    extends: [...vue.configs['flat/recommended']],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
    rules: { 'vue/block-lang': ['error', { script: { lang: 'ts' } }] }
  },
  {
    files: ['tests/core/**/*.ts', 'vite.platform.config.ts'],
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      globals: { ...globals.node, ...globals.browser },
      parserOptions: { project: './tsconfig.tests.json', tsconfigRootDir: import.meta.dirname }
    },
    rules: { '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }] }
  },
  prettier
)
