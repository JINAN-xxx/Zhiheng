import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

for (const fileName of ['bin/nginx.dockermy.conf', 'bin/nginx.dockerpg.conf']) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /gzip_static\s+on;/)
  assert.match(source, /location \^~ \/static\//)
  assert.match(source, /location \^~ \/monacoeditorwork\//)
  assert.match(source, /Cache-Control "public, max-age=31536000, immutable"/)
}

console.log('ok nginx static performance contract')
