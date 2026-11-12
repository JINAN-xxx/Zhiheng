import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import { moduleBoundaries } from './tools/moduleBoundaries.js'

const coreFiles = ['src/{app,shared,modules}/**/*.{ts,tsx,vue}', 'src/vite-env.d.ts']

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    ...js.configs.recommended,
    files: ['eslint.config.js', 'tools/**/*.js'],
    languageOptions: { globals: globals.node }
  },
  {
    files: coreFiles,
    plugins: { architecture: { rules: { 'module-boundaries': moduleBoundaries } } },
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
      'architecture/module-boundaries': 'error',
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
  {
    files: coreFiles,
    ignores: ['src/shared/requestId.ts', 'src/shared/clipboard.ts'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression[callee.object.name='crypto'][callee.property.name='randomUUID']",
          message: 'Use shared/createRequestId so requests also work on LAN HTTP.'
        },
        {
          selector:
            "CallExpression[callee.object.property.name='clipboard'][callee.property.name='writeText']",
          message: 'Use shared/copyText so copying also works on LAN HTTP.'
        }
      ]
    }
  },
  prettier
)
